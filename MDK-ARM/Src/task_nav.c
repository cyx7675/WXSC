/**
 * task_nav.c — CAN2 发送任务：打包 IMU/GPS 数据 → U34 ECU
 *
 * 方案A（F4 传感器前端 + U34 EKF）：
 *   F4 不再本地跑 EKF，只做数据搬运：
 *   1. 阻塞等待 EVT_IMU_READY（200Hz）→ 读 g_imu_data → 打包 4 帧 → CAN2 发给 U34
 *   2. 非阻塞检查 EVT_GPS_READY（10Hz）→ 读 g_gps_data → 打包 5 帧 → CAN2 发给 U34
 *
 * U34 收到后运行 EKF（Simulink），结果通过 CAN2 回传 → task_can2.c 接收
 *
 * 优先级 7（最高），按 IMU 周期（200Hz = 5ms）运行
 */

#include "task_imu_rx.h"
#include "task_gps_rx.h"
#include "app_types.h"
#include "app_queues.h"
#include "main.h"
#include "can.h"
#include "cmsis_os2.h"
#include <string.h>

/* ---- 调试计数器（定义在 main.c，Keil Watch 窗口查看） ---- */
extern volatile uint32_t dbg_nav_loop, dbg_can2_tx;

/* ---- 声明其他任务声明的全局变量 ---- */
extern imu_data_t  g_imu_data;
extern gps_data_t  g_gps_data;

/* ---- 发送序号 ---- */
static uint8_t imu_seq = 0;
static uint8_t gps_seq = 0;

/* ================================================================
 * 诊断计数器（Keil Watch 窗口观察，用于区分「帧没发」与「字段为0」）
 *
 * 排查「0x113 / 0x124 的 tick 和 seq 都是 0」时的判据：
 *   dbg_imu_grp == 0        → EVT_IMU_READY 没触发，IMU 数据根本没进 F4
 *   dbg_imu_grp>0 但 PCAN 抓不到 0x113 → can2_send 失败/邮箱被占（看 dbg_can2_fail）
 *   dbg_tick_imu_last == 0  → imu.tick 本身是 0（IMU 的 0x52 帧没进来）
 *   以上都正常但 PCAN 显示 0 → TIR 标准帧 ID 域写法有问题（见 can2_send 注释）
 * ================================================================ */
volatile uint32_t dbg_imu_grp       = 0;   /* 发出的 IMU 组数（每组 4 帧） */
volatile uint32_t dbg_gps_grp       = 0;   /* 发出的 GPS 组数（每组 5 帧） */
volatile uint32_t dbg_can2_fail     = 0;   /* can2_send 找不到空邮箱而失败的次数 */
volatile uint32_t dbg_tick_imu_last = 0;   /* 最近一次打进 0x113 的 imu.tick */
volatile uint32_t dbg_seq_imu_last  = 0;   /* 最近一次打进 0x113 的 imu_seq（+1 后的值）*/
volatile uint32_t dbg_tick_gps_last = 0;   /* 最近一次打进 0x124 的 gps.tick */
volatile uint32_t dbg_seq_gps_last  = 0;   /* 最近一次打进 0x124 的 gps_seq */
volatile uint8_t  dbg_evt_imu_stuck = 0;   /* 连续多少轮没拿到 EVT_IMU_READY */
volatile uint8_t  dbg_evt_gps_stuck = 0;   /* 连续多少轮没拿到 EVT_GPS_READY */

/* ================================================================
 * CAN2 发送（任务上下文）
 * 注意：HAL_CAN_AddTxMessage 用 TSR.CODE 选邮箱，但 CODE 可能指向
 * 忙的邮箱（TME=0），HAL 会写入但被 bxCAN 硬件忽略（TXRQ 置位
 * 后寄存器只读），新帧丢失。所以这里绕过 HAL，自己按 TME[0:2]
 * 顺序找空邮箱。
 * ================================================================ */
static int can2_send(uint32_t id, const uint8_t *data, uint8_t dlc)
{
    uint32_t t0 = HAL_GetTick();
    while (1) {
        uint32_t tsr = hcan2.Instance->TSR;
        /* 按优先级 0 > 1 > 2 自己找空邮箱 */
        if (tsr & CAN_TSR_TME0) {
            hcan2.Instance->sTxMailBox[0].TIR  = (id << CAN_TI0R_STID_Pos);
            hcan2.Instance->sTxMailBox[0].TDTR = (dlc & 0x0F);
            hcan2.Instance->sTxMailBox[0].TDHR = ((uint32_t)data[7] << 24) |
                                                  ((uint32_t)data[6] << 16) |
                                                  ((uint32_t)data[5] << 8)  | data[4];
            hcan2.Instance->sTxMailBox[0].TDLR = ((uint32_t)data[3] << 24) |
                                                  ((uint32_t)data[2] << 16) |
                                                  ((uint32_t)data[1] << 8)  | data[0];
            hcan2.Instance->sTxMailBox[0].TIR |= CAN_TI0R_TXRQ;   /* 请求发送 */
            dbg_can2_tx++;
            return HAL_OK;
        }
        if (tsr & CAN_TSR_TME1) {
            hcan2.Instance->sTxMailBox[1].TIR  = (id << CAN_TI0R_STID_Pos);
            hcan2.Instance->sTxMailBox[1].TDTR = (dlc & 0x0F);
            hcan2.Instance->sTxMailBox[1].TDHR = ((uint32_t)data[7] << 24) |
                                                  ((uint32_t)data[6] << 16) |
                                                  ((uint32_t)data[5] << 8)  | data[4];
            hcan2.Instance->sTxMailBox[1].TDLR = ((uint32_t)data[3] << 24) |
                                                  ((uint32_t)data[2] << 16) |
                                                  ((uint32_t)data[1] << 8)  | data[0];
            hcan2.Instance->sTxMailBox[1].TIR |= CAN_TI0R_TXRQ;
            dbg_can2_tx++;
            return HAL_OK;
        }
        if (tsr & CAN_TSR_TME2) {
            hcan2.Instance->sTxMailBox[2].TIR  = (id << CAN_TI0R_STID_Pos);
            hcan2.Instance->sTxMailBox[2].TDTR = (dlc & 0x0F);
            hcan2.Instance->sTxMailBox[2].TDHR = ((uint32_t)data[7] << 24) |
                                                  ((uint32_t)data[6] << 16) |
                                                  ((uint32_t)data[5] << 8)  | data[4];
            hcan2.Instance->sTxMailBox[2].TDLR = ((uint32_t)data[3] << 24) |
                                                  ((uint32_t)data[2] << 16) |
                                                  ((uint32_t)data[1] << 8)  | data[0];
            hcan2.Instance->sTxMailBox[2].TIR |= CAN_TI0R_TXRQ;
            dbg_can2_tx++;
            return HAL_OK;
        }
        /* 没有空邮箱：等。200μs/帧，发完 3 帧 < 1ms，给 5ms 余量足够 */
        if ((HAL_GetTick() - t0) > 5) {
            dbg_can2_fail++;
            return HAL_ERROR;
        }
        osThreadYield();
    }
}

/* ---- 小端打包辅助 ---- */
static void pack_float(uint8_t *buf, float val)
{
    memcpy(buf, &val, 4);/*memcpy是复写*/
}
static void pack_u32(uint8_t *buf, uint32_t val)
{
    memcpy(buf, &val, 4);
}
static void pack_double(uint8_t *buf, double val)
{
    memcpy(buf, &val, 8);
}

/* ================================================================
 * 打包 IMU 数据 → 4 帧 CAN2 → 发给 U34
 * 帧 ID: 0x110~0x113
 * ================================================================ */
static void send_imu_to_u34(const imu_data_t *imu)
{
    uint8_t buf[8];

    /* 0x110: accel[0] + accel[1] */
    pack_float(buf + 0, imu->accel[0]);
    pack_float(buf + 4, imu->accel[1]);
    can2_send(CAN2_ID_IMU_0, buf, 8);

    /* 0x111: accel[2] + gyro[0] */
    pack_float(buf + 0, imu->accel[2]);
    pack_float(buf + 4, imu->gyro[0]);
    can2_send(CAN2_ID_IMU_1, buf, 8);

    /* 0x112: gyro[1] + gyro[2] */
    pack_float(buf + 0, imu->gyro[1]);
    pack_float(buf + 4, imu->gyro[2]);
    can2_send(CAN2_ID_IMU_2, buf, 8);

    /* 0x113: tick + seq + valid + rsv */
    pack_u32(buf + 0, imu->tick);
    buf[4] = imu_seq++;
    buf[5] = 1;             /* valid */
    buf[6] = 0;
    buf[7] = 0;
    dbg_tick_imu_last = imu->tick;   /* 诊断：确认 tick 不是 0 */
    dbg_seq_imu_last  = buf[4];     /* 诊断：确认 seq 在递增 */
    if (can2_send(CAN2_ID_IMU_3, buf, 8) == HAL_OK)
        dbg_imu_grp++;
}

/* ================================================================
 * 打包 GPS 数据 → 5 帧 CAN2 → 发给 U34
 * 帧 ID: 0x120~0x124
 * ================================================================ */
static void send_gps_to_u34(const gps_data_t *gps)
{
    uint8_t buf[8];

    /* 0x120: lat(double, 8字节) */
    pack_double(buf, gps->lat);
    can2_send(CAN2_ID_GPS_0, buf, 8);

    /* 0x121: lon(double, 8字节) */
    pack_double(buf, gps->lon);
    can2_send(CAN2_ID_GPS_1, buf, 8);

    /* 0x122: speed + heading */
    pack_float(buf + 0, gps->speed);
    pack_float(buf + 4, gps->heading);
    can2_send(CAN2_ID_GPS_2, buf, 8);

    /* 0x123: alt + fix + sats + rsv */
    pack_float(buf + 0, gps->alt);
    buf[4] = gps->fix_quality;
    buf[5] = gps->sats;
    buf[6] = 0;
    buf[7] = 0;
    can2_send(CAN2_ID_GPS_3, buf, 8);

    /* 0x124: tick + seq + valid + rsv */
    pack_u32(buf + 0, gps->tick);
    buf[4] = gps_seq++; // buf[4]   — 序列号，每发一组+1，U34用来检测丢帧
    buf[5] = (gps->fix_quality > 0) ? 1 : 0; // buf[5]   — valid标志，1=数据有效
    buf[6] = 0;
    buf[7] = 0;
    dbg_tick_gps_last = gps->tick;   /* 诊断：确认 tick 不是 0 */
    dbg_seq_gps_last  = buf[4];     /* 诊断：确认 seq 在递增 */
    if (can2_send(CAN2_ID_GPS_4, buf, 8) == HAL_OK)
        dbg_gps_grp++;
}

/* ================================================================
 * 任务入口
 * ================================================================ */
void StartTask_nav(void *argument)
{
    uint32_t events;

    for (;;) {
        /* 阻塞等待 IMU 就绪（200Hz）。用有限超时而非 osWaitForever，
         * 这样即使 IMU 断线也能继续跑并刷新诊断计数器。 */
        events = osEventFlagsWait(eg_sensorHandle,
            EVT_IMU_READY,
            osFlagsWaitAll,
            100);

        if ((events & EVT_IMU_READY) == 0) {
            /* IMU 没来：只有这种情况任务才会走到这里（超时返回） */
            dbg_evt_imu_stuck++;
            dbg_nav_loop++;      /* 保持 dbg_nav_loop 递增，便于看任务还活着 */
            continue;
        }

        dbg_nav_loop++;

        /* ---- 读 g_imu_data（拿锁，防止 task_imu_rx 同时写） ---- */
        if (osMutexAcquire(myMutex_nav_resultHandle, osWaitForever) == osOK) {
            imu_data_t imu_snap = g_imu_data;
            osMutexRelease(myMutex_nav_resultHandle);
            send_imu_to_u34(&imu_snap);
        }

        /* ---- 非阻塞检查 GPS（10Hz，大部分周期无 GPS） ---- */
        events = osEventFlagsGet(eg_sensorHandle);/* 轮询先读标志位，避免阻塞等待 */
        if (events & EVT_GPS_READY) {
            osEventFlagsClear(eg_sensorHandle, EVT_GPS_READY);
            if (osMutexAcquire(myMutex_nav_resultHandle, osWaitForever) == osOK) {
                gps_data_t gps_snap = g_gps_data;
                osMutexRelease(myMutex_nav_resultHandle);
                send_gps_to_u34(&gps_snap);
            }
        } else {
            dbg_evt_gps_stuck++;
        }
    }
}
