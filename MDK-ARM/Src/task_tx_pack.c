/**
 * task_tx_pack.c — 数据打包 + UART1 DMA 发送到无线数传
 *
 * 改为两个独立包：
 *   1. ADC 包（200Hz）：task_adc 写完 g_adc_data 后通知 EVT_ADC_READY
 *   2. NAV 包（50Hz）：task_can2 收到 U34 EKF 结果后推入 q_nav_resultHandle
 *
 * 优先级 4，双事件驱动
 * UART1 DMA 发送不阻塞 CPU，串行化（同一时刻只发一包）
 */

#include "app_types.h"
#include "app_queues.h"
#include "main.h"
#include "usart.h"
#include "cmsis_os2.h"
#include <string.h>

/* ---- 声明外部变量 ---- */
extern adc_data_t g_adc_data;

/* ---- 包序号（ADC / NAV / MOTOR 独立计数） ---- */
static uint8_t adc_seq   = 0;
static uint8_t nav_seq   = 0;
static uint8_t motor_seq = 0;

/* NAV 发送分频：U34 每 20ms 来一组 EKF 结果（50Hz），
 * MOTOR 每组都发(50Hz)，NAV 隔一组发一次(25Hz)。 */
static uint8_t nav_div   = 0;

/* ================================================================
 * DMA 发送缓冲区 —— 必须是 static，绝对不能放在栈上！
 *
 * HAL_UART_Transmit_DMA 是异步的：函数返回后 DMA 还在逐字节读这块内存
 * （27 字节 @115200 需要约 2.3ms）。如果缓冲区是函数内的局部变量，
 * 函数一返回栈帧就失效，后续函数调用的栈帧会覆盖它，DMA 读到的是垃圾。
 * ================================================================ */
static tx_adc_packet_t   g_tx_adc_pkt;
static tx_nav_packet_t   g_tx_nav_pkt;
static tx_motor_packet_t g_tx_motor_pkt;

/* ================================================================
 * 诊断计数器（Keil Watch 窗口观察）
 * ================================================================ */
volatile uint32_t dbg_tx_adc_try   = 0;   /* 进入 send_adc_packet 的次数 */
volatile uint32_t dbg_tx_adc_mutex = 0;   /* 拿不到 ADC 互斥锁的次数 */
volatile uint32_t dbg_tx_adc_ok    = 0;   /* ADC 包 DMA 启动成功次数 */
volatile uint32_t dbg_tx_adc_busy  = 0;   /* DMA 忙、丢弃该包的次数 */
volatile uint32_t dbg_tx_done_to   = 0;   /* 等发送完成超时次数（应始终为 0） */
volatile uint32_t dbg_tx_nav_ok    = 0;   /* NAV 包发送次数（恒 0 = U34 没回数据） */
volatile uint32_t dbg_tx_motor_ok  = 0;   /* MOTOR 包发送次数 */
volatile uint32_t dbg_evt_timeout  = 0;   /* 等 EVT_ADC_READY 超时次数 */
volatile uint8_t  dbg_last_pkt[27] = {0}; /* 交给 DMA 之前的包快照，用于比对空中数据 */

/* ================================================================
 * E34 分包空闲间隔（方案 B）
 *
 * E34 半双工模式：串口连续输入时，若两包间隔 <「3 字节时间」(260µs@115200)，
 * 模块不会 flush，会把相邻逻辑包拼成连续流再按 29B 重切 —— 后果是丢 1 个
 * 空包会同时伤到 2 个逻辑包（跨边界），丢包隔离性变差。
 * 因此在 NAV / MOTOR 发送前插入一段空闲，让每个逻辑包独立成一个空包。
 *
 * ADC 包不需要：它 5ms 一包，发完有约 2.66ms 空闲，本来就干净 flush。
 *
 * 取值依据：260µs(3字节时间) + 87µs(上一包最后 1 字节仍在移位)
 *           + 约 70µs 余量 ≈ 420µs
 * ================================================================ */
#define E34_IDLE_GAP_US   420u

/* ---- 微秒级忙等（基于 DWT 周期计数器，必须先调 dwt_cyccnt_init） ----
 * 用 HAL_RCC_GetHCLKFreq() 而非 SystemCoreClock，避免后者可见性导致的编译问题。 */
static uint32_t g_cycles_per_us = 168;    /* 兜底 168MHz，init 时按实际时钟校正 */

static void dwt_cyccnt_init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;   /* 使能跟踪单元 */
    DWT->CYCCNT = 0;
    DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;             /* 启动周期计数 */
    g_cycles_per_us = HAL_RCC_GetHCLKFreq() / 1000000U;
}

/* 无符号减法天然处理 32 位回绕（168MHz 下约 25.6s 回绕一次） */
static void delay_us(uint32_t us)
{
    uint32_t start = DWT->CYCCNT;
    uint32_t span  = us * g_cycles_per_us;
    while ((uint32_t)(DWT->CYCCNT - start) < span) { __NOP(); }
}

/* ================================================================
 * E34 一次性配置：把模块 UART 波特率从出厂 9600 改成 115200
 *
 * 使用步骤：
 *   1) 模块 M0=1、M1=1（设置模式，此模式下模块只认 9600 8N1）
 *   2) E34_CFG_ONCE 置 1，编译烧录，上电即自动下发
 *   3) Keil Watch 看 e34_cfg_rx[6]，等于 C0 00 00 38 00 40 即成功
 *   4) 改回 0 重新烧录，M0/M1 拨回 0/0 断电重启
 * ================================================================ */
#define E34_CFG_ONCE  0    /* 配置成功后改回 0 */

#if E34_CFG_ONCE
volatile uint8_t e34_cfg_rx[6] = {0};   /* 模块应答，Watch 窗口看这个 */

static void e34_config_once(void)
{
    static const uint8_t cmd[6] = {0xC0, 0x00, 0x00, 0x38, 0x00, 0x40};

    /* 切到 9600（设置模式下模块只听得懂这个速率） */
    huart2.Init.BaudRate = 9600;
    HAL_UART_Init(&huart2);
    osDelay(100);                       /* 等模块 AUX 就绪 */
    __HAL_UART_CLEAR_OREFLAG(&huart2);

    HAL_UART_Transmit(&huart2, (uint8_t *)cmd, 6, 300);
    HAL_UART_Receive(&huart2, (uint8_t *)e34_cfg_rx, 6, 500);

    /* 恢复 115200，后面正常发数据 */
    huart2.Init.BaudRate = 115200;
    HAL_UART_Init(&huart2);
}
#endif

/* ================================================================
 * CRC-16/MODBUS（查表法）
 * ================================================================ */
static const uint16_t crc16_table[256] = {
    0x0000,0xC0C1,0xC181,0x0140,0xC301,0x03C0,0x0280,0xC241,
    0xC601,0x06C0,0x0780,0xC741,0x0500,0xC5C1,0xC481,0x0440,
    0xCC01,0x0CC0,0x0D80,0xCD41,0x0F00,0xCFC1,0xCE81,0x0E40,
    0x0A00,0xCAC1,0xCB81,0x0B40,0xC901,0x09C0,0x0880,0xC841,
    0xD801,0x18C0,0x1980,0xD941,0x1B00,0xDBC1,0xDA81,0x1A40,
    0x1E00,0xDEC1,0xDF81,0x1F40,0xDD01,0x1DC0,0x1C80,0xDC41,
    0x1400,0xD4C1,0xD581,0x1540,0xD701,0x17C0,0x1680,0xD641,
    0xD201,0x12C0,0x1380,0xD341,0x1100,0xD1C1,0xD081,0x1040,
    0xF001,0x30C0,0x3180,0xF141,0x3300,0xF3C1,0xF281,0x3240,
    0x3600,0xF6C1,0xF781,0x3740,0xF501,0x35C0,0x3480,0xF441,
    0x3C00,0xFCC1,0xFD81,0x3D40,0xFF01,0x3FC0,0x3E80,0xFE41,
    0xFA01,0x3AC0,0x3B80,0xFB41,0x3900,0xF9C1,0xF881,0x3840,
    0x2800,0xE8C1,0xE981,0x2940,0xEB01,0x2BC0,0x2A80,0xEA41,
    0xEE01,0x2EC0,0x2F80,0xEF41,0x2D00,0xEDC1,0xEC81,0x2C40,
    0xE401,0x24C0,0x2580,0xE541,0x2700,0xE7C1,0xE681,0x2640,
    0x2200,0xE2C1,0xE381,0x2340,0xE101,0x21C0,0x2080,0xE041,
    0xA001,0x60C0,0x6180,0xA141,0x6300,0xA3C1,0xA281,0x6240,
    0x6600,0xA6C1,0xA781,0x6740,0xA501,0x65C0,0x6480,0xA441,
    0x6C00,0xACC1,0xAD81,0x6D40,0xAF01,0x6FC0,0x6E80,0xAE41,
    0xAA01,0x6AC0,0x6B80,0xAB41,0x6900,0xA9C1,0xA881,0x6840,
    0x7800,0xB8C1,0xB981,0x7940,0xBB01,0x7BC0,0x7A80,0xBA41,
    0xBE01,0x7EC0,0x7F80,0xBF41,0x7D00,0xBDC1,0xBC81,0x7C40,
    0xB401,0x74C0,0x7580,0xB541,0x7700,0xB7C1,0xB681,0x7640,
    0x7200,0xB2C1,0xB381,0x7340,0xB101,0x71C0,0x7080,0xB041,
    0x5000,0x90C1,0x9181,0x5140,0x9301,0x53C0,0x5280,0x9241,
    0x9601,0x56C0,0x5780,0x9741,0x5500,0x95C1,0x9481,0x5440,
    0x9C01,0x5CC0,0x5D80,0x9D41,0x5F00,0x9FC1,0x9E81,0x5E40,
    0x5A00,0x9AC1,0x9B81,0x5B40,0x9901,0x59C0,0x5880,0x9841,
    0x8801,0x48C0,0x4980,0x8941,0x4B00,0x8BC1,0x8A81,0x4A40,
    0x4E00,0x8EC1,0x8F81,0x4F40,0x8D01,0x4DC0,0x4C80,0x8C41,
    0x4400,0x84C1,0x8581,0x4540,0x8701,0x47C0,0x4680,0x8641,
    0x8201,0x42C0,0x4380,0x8341,0x4100,0x81C1,0x8081,0x4040
};

static uint16_t crc16_modbus(const uint8_t *data, uint16_t len)
{
    uint16_t crc = 0xFFFF;
    while (len--) crc = (crc >> 8) ^ crc16_table[(crc ^ *data++) & 0xFF];
    return crc;
}

/* ================================================================
 * 12-bit 打包：10 个 uint16 值（均 < 4096）→ 15 字节
 * 每 2 个值占 3 字节：[v0_hi8] [v0_lo4|v1_hi4] [v1_lo8]
 * 解包端：v[i]   = (buf[j] << 4) | (buf[j+1] >> 4)
 *         v[i+1] = ((buf[j+1] & 0xF) << 8) | buf[j+2]
 * ================================================================ */
static void pack_adc12(const uint16_t src[10], uint8_t dst[15])
{
    for (int i = 0; i < 10; i += 2) {
        int j = (i / 2) * 3;
        dst[j]     = (uint8_t)(src[i] >> 4);
        dst[j + 1] = (uint8_t)((src[i] & 0x0F) << 4) |
                     (uint8_t)((src[i + 1] >> 8) & 0x0F);
        dst[j + 2] = (uint8_t)(src[i + 1] & 0xFF);
    }
}

/* ================================================================
 * 发送 ADC 数据包（200Hz，27 字节）
 * ================================================================ */
static void send_adc_packet(void)
{
    uint16_t adc_vals[10];

    dbg_tx_adc_try++;

    /* 读 ADC 原始值（互斥锁保护，仅做 memcpy，不长期持有锁） */
    if (osMutexAcquire(myMutex_adcHandle, osWaitForever) != osOK) {
        dbg_tx_adc_mutex++;
        return;
    }
    memcpy(adc_vals, g_adc_data.raw, sizeof(adc_vals));
    uint32_t tick = g_adc_data.tick;
    osMutexRelease(myMutex_adcHandle);

    /* 等上一包 DMA 真正发完（与 NAV 共用同一串口，必须串行）。
     * 这一步保证：此刻 g_tx_adc_pkt 没被任何在途 DMA 占用。 */
    if (osSemaphoreAcquire(sem_tx_doneHandle, 50) != osOK) {
        dbg_tx_adc_busy++;
        return;                         /* 上一包卡住，丢这包 */
    }

    /* 填充 static 发送缓冲 —— 绝不能是函数内局部变量！
     * HAL_UART_Transmit_DMA 是异步的，函数返回后 DMA 还要读这块内存约 2.3ms，
     * 若放在栈上，栈帧一退出就被后续调用覆盖 → 空中全是乱码（0xA5）。 */
    g_tx_adc_pkt.header    = 0xAA55;
    g_tx_adc_pkt.type      = 0;         /* 0=ADC 包 */
    g_tx_adc_pkt.seq        = adc_seq++;
    g_tx_adc_pkt.timestamp = tick;

    /* 12-bit 打包 10 路原始值 → 15 字节（无损，原始值必 < 4096） */
    pack_adc12(adc_vals, g_tx_adc_pkt.adc_packed);

    /* CRC 覆盖 header 到 adc_packed 全部字段 */
    g_tx_adc_pkt.crc16 = crc16_modbus((uint8_t*)&g_tx_adc_pkt,
        sizeof(tx_adc_packet_t) - 4);   /* 不含 crc16 + tail */
    g_tx_adc_pkt.tail = 0x55AA;

    /* 发前快照，供 Keil Watch 比对空中数据（期望与串口抓到的字节一致）。
     * dbg_last_pkt 带 volatile，memcpy 要 void*，须显式去 volatile（#167-D） */
    memcpy((void *)dbg_last_pkt, &g_tx_adc_pkt, sizeof(dbg_last_pkt));

    /* 启动 DMA（异步）。完成时在 HAL_UART_TxCpltCallback 释放 sem */
    if (HAL_UART_Transmit_DMA(&huart2, (uint8_t*)&g_tx_adc_pkt,
            sizeof(tx_adc_packet_t)) != HAL_OK) {
        osSemaphoreRelease(sem_tx_doneHandle);   /* DMA 没启动，令牌还回去 */
        dbg_tx_adc_busy++;
        return;
    }
    dbg_tx_adc_ok++;
}

/* ================================================================
 * 发送导航数据包（25Hz，22 字节）
 * 内容：姿态 roll/pitch/yaw + 横向加速度 + 纵向加速度（慢变量，25Hz 足够）
 * ================================================================ */
static void send_nav_packet(const nav_result_t *nav)
{
    dbg_tx_nav_ok++;

    /* 等上一包 DMA 发完（与 ADC 共用 USART2，必须串行） */
    if (osSemaphoreAcquire(sem_tx_doneHandle, 50) != osOK) {
        return;                         /* 上一包卡住，丢这包 */
    }

    /* 插入 ≥260µs 空闲，让 E34 把上一包(ADC)单独 flush 成一个空包，
     * 避免与本包拼接后按 29B 重切（详见 E34_IDLE_GAP_US 注释） */
    delay_us(E34_IDLE_GAP_US);

    /* 全部写入 static 缓冲 g_tx_nav_pkt（绝不能放栈上，原因同 ADC） */
    g_tx_nav_pkt.header    = 0xAA55;
    g_tx_nav_pkt.type      = 1;         /* 1=NAV 包 */
    g_tx_nav_pkt.seq       = nav_seq++;
    g_tx_nav_pkt.timestamp = nav->tick;
    /* 姿态 int16×100 量化：0.01° 精度，无损 */
    g_tx_nav_pkt.roll  = (int16_t)(nav->roll  * 100.0f);
    g_tx_nav_pkt.pitch = (int16_t)(nav->pitch * 100.0f);
    g_tx_nav_pkt.yaw   = (int16_t)(nav->yaw   * 100.0f);
    g_tx_nav_pkt.lat_accel   = nav->lat_accel;     /* 横向加速度 raw 有符号，U34 定标 */
    g_tx_nav_pkt.longi_accel = nav->longi_accel;   /* 纵向加速度 raw 有符号，U34 定标 */

    /* CRC 覆盖 header 到 longi_accel 全部字段 */
    g_tx_nav_pkt.crc16 = crc16_modbus((uint8_t*)&g_tx_nav_pkt,
        sizeof(tx_nav_packet_t) - 4);   /* 不含 crc16 + tail */
    g_tx_nav_pkt.tail = 0x55AA;

    /* 启动 DMA（异步）。完成时在 HAL_UART_TxCpltCallback 释放 sem */
    if (HAL_UART_Transmit_DMA(&huart2, (uint8_t*)&g_tx_nav_pkt,
            sizeof(tx_nav_packet_t)) != HAL_OK) {
        osSemaphoreRelease(sem_tx_doneHandle);   /* DMA 没启动，令牌还回去 */
        return;
    }
}

/* ================================================================
 * 发送车辆/电机数据包（50Hz，23 字节）
 * 数据来自 U34 的 CAN2 0x212/0x213，raw 透传不在 F4 定标
 * （量程/换算由 U34 与上位机约定，F4 只做搬运）。
 * 单独成包是为了和 NAV 都控制在 E34 单帧 29B 以内。
 *
 * ★ 车速(speed) 放在这里：**滑移率 = (轮速 − 车速)/车速**，
 *   轮速来自 motor_rpm、车速来自 speed，两者同包同时间戳同频率(50Hz)，
 *   上位机收到一包即可直接算打滑，无需跨包对齐。
 * ================================================================ */
static void send_motor_packet(const nav_result_t *nav)
{
    dbg_tx_motor_ok++;

    /* 与 ADC/NAV 共用 USART2，先拿令牌。
     * 此时上一包（ADC）的 DMA 可能还在飞，拿到令牌即代表可以安全填缓冲。 */
    if (osSemaphoreAcquire(sem_tx_doneHandle, 50) != osOK) {
        return;
    }

    /* 插入空闲，让上一包(ADC)单独成一个空包，不与本包拼接重切 */
    delay_us(E34_IDLE_GAP_US);

    g_tx_motor_pkt.header     = 0xAA55;
    g_tx_motor_pkt.type       = 2;              /* 2=MOTOR 包 */
    g_tx_motor_pkt.seq        = motor_seq++;
    g_tx_motor_pkt.timestamp  = nav->tick;      /* 本包内 rpm 与 speed 共用此时间戳 */
    g_tx_motor_pkt.motor_temp = nav->motor_temp;
    g_tx_motor_pkt.motor_rpm  = nav->motor_rpm;   /* → 轮速 */
    g_tx_motor_pkt.torque     = nav->torque;
    g_tx_motor_pkt.throttle   = nav->throttle;
    g_tx_motor_pkt.voltage    = nav->voltage;
    g_tx_motor_pkt.current    = nav->current;
    g_tx_motor_pkt.speed      = (int16_t)(nav->speed * 100.0f);  /* 车速 ×100，0.01 m/s */

    /* CRC 覆盖 header 到 speed 全部字段 */
    g_tx_motor_pkt.crc16 = crc16_modbus((uint8_t*)&g_tx_motor_pkt,
        sizeof(tx_motor_packet_t) - 4);   /* 不含 crc16 + tail */
    g_tx_motor_pkt.tail = 0x55AA;

    if (HAL_UART_Transmit_DMA(&huart2, (uint8_t*)&g_tx_motor_pkt,
            sizeof(tx_motor_packet_t)) != HAL_OK) {
        osSemaphoreRelease(sem_tx_doneHandle);
        return;
    }
}

/* ================================================================
 * 任务入口
 *
 * 主循环：等待 ADC 就绪 + 检查 NAV 队列，两个独立发送、串行化
 * ================================================================ */
void StartTask_tx_pack(void *argument)
{
    nav_result_t *nav;

    /* 启动 DWT 周期计数器 —— delay_us() 依赖它。
     * 必须先init，否则 CYCCNT 恒为 0，delay_us 会死循环。 */
    dwt_cyccnt_init();

#if E34_CFG_ONCE
    e34_config_once();          /* 只在开机跑一次，配完记得把宏改回 0 */
#endif

    for (;;) {
        /* 等待 ADC 数据就绪（最长等 5ms，超时后检查 NAV） */
        uint32_t events = osEventFlagsWait(eg_sensorHandle,
            EVT_ADC_READY, osFlagsWaitAny, 5);

        /* osEventFlagsWait 出错时返回的是错误码（最高位为 1），
         * 必须先用 (int32_t)events < 0 排除，再判定位，避免误触发发送 */
        if ((int32_t)events < 0) {
            if (events == osFlagsErrorTimeout) dbg_evt_timeout++;
        } else if (events & EVT_ADC_READY) {
            send_adc_packet();
        }

        /* U34 每 20ms 来一组 EKF 结果（50Hz）：
         *   MOTOR 50Hz —— 每组都发。打滑判据(rpm+speed)在这里，瞬态不能丢。
         *   NAV   25Hz —— 隔一组发一次（姿态+加速度是慢变量，25Hz 足够）。
         * MOTOR 先发：它是 50Hz 关键包，先发可少等约 2.4ms。 */
        if (osMessageQueueGet(q_nav_resultHandle, &nav, NULL, 0) == osOK) {
            send_motor_packet(nav);
            if ((nav_div++ & 1U) == 0U) {
                send_nav_packet(nav);
            }
        }
    }
}
