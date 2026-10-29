/**
 * task_can2.c — CAN2 接收任务：接收 U34 ECU 回传的 EKF 结果
 *
 * 方案A（F4 传感器前端 + U34 EKF）：
 *   U34 运行 EKF 后，将结果打包成 6 帧 CAN2（0x210~0x215）发回 F4
 *   本任务负责：
 *   1. 阻塞等待 q_can2_rx（CAN2 ISR 投递的帧）
 *   2. 按帧 ID 组装 6 帧为完整 nav_result_t
 *   3. 组装完成后写入静态池 → 推入 q_nav_result（指针）
 *   4. task_tx_pack 从 q_nav_result 取最新结果 → UART1 发数传
 *
 * 组装策略：收到 0x210 视为新组开始（丢弃不完整的上一组）
 * 优先级 5，事件驱动
 */
#include "task_can2.h"
#include "app_types.h"
#include "app_queues.h"
#include "main.h"
#include "cmsis_os2.h"
#include <string.h>

/* ---- 小端解包辅助 ---- */
static float unpack_float(const uint8_t *buf)
{
    float val;
    memcpy(&val, buf, 4);
    return val;
}
static uint16_t unpack_u16(const uint8_t *buf)   /* 小端 u16（U34 侧也是小端） */
{
    return (uint16_t)buf[0] | ((uint16_t)buf[1] << 8);
}
static int16_t unpack_i16(const uint8_t *buf)    /* 小端有符号 i16 */
{
    return (int16_t)((uint16_t)buf[0] | ((uint16_t)buf[1] << 8));
}
/* 注：unpack_double 已移除 —— 0x212/0x213 不再承载 lat/lon，全工程无调用。
 * 若以后再传 double 字段，按同款 memcpy 方式加回即可。 */

/* ================================================================
 * EKF 结果组装状态机
 * ================================================================ */
static nav_result_t  rx_assembly;       /* 组装缓冲区 */
static uint8_t       rx_frame_mask;      /* 已收帧位图（bit0=0x210, ...bit5=0x215） */
static uint32_t      rx_first_tick;      /* 首帧时间戳，用于超时判断 */

/* ---- 静态池（供 q_nav_result 传指针） ---- */
#define NAV_POOL_SIZE 4
static nav_result_t nav_pool[NAV_POOL_SIZE];
static uint8_t      nav_pool_wr = 0;

/* ---- 处理单帧 ---- */
static void process_ekf_frame(const can_frame_t *f)
{
    uint8_t idx;

    /* 映射 ID → 帧序号 */
    switch (f->id) {
    case CAN2_ID_EKF_0: idx = 0; break;
    case CAN2_ID_EKF_1: idx = 1; break;
    case CAN2_ID_EKF_2: idx = 2; break;
    case CAN2_ID_EKF_3: idx = 3; break;
    case CAN2_ID_EKF_4: idx = 4; break;
    case CAN2_ID_EKF_5: idx = 5; break;
    default: return;  /* 非 EKF 结果帧，忽略 */
    }

    /* 收到首帧 → 开始新组装（丢弃不完整的上一组） */
    if (idx == 0) {
        memset(&rx_assembly, 0, sizeof(rx_assembly));
        rx_frame_mask = 0;
        rx_first_tick = HAL_GetTick();
    }

    /* 超时保护：如果距首帧超过 50ms，丢弃当前组并重启 */
    if (rx_frame_mask != 0 && (HAL_GetTick() - rx_first_tick) > 50) {
        memset(&rx_assembly, 0, sizeof(rx_assembly));
        rx_frame_mask = 0;
        if (idx != 0) return;  /* 超时后只接受首帧 */
        rx_first_tick = HAL_GetTick();
    }

    /* 已收过该帧（重复帧），忽略 */
    if (rx_frame_mask & (1 << idx)) 
        return;

    /* 按帧序号解包数据 */
    switch (idx) {
    case 0:  /* 0x210: roll + pitch */
        rx_assembly.roll  = unpack_float(f->data + 0);
        rx_assembly.pitch = unpack_float(f->data + 4);
        break;
    case 1:  /* 0x211: yaw + speed */
        rx_assembly.yaw   = unpack_float(f->data + 0);
        rx_assembly.speed = unpack_float(f->data + 4);
        break;
    case 2:  /* 0x212: 电机温度 + 电机转速 + 扭矩 + 油门开度 + 横向加速度(有符号 i16) */
        rx_assembly.motor_temp = f->data[0];
        rx_assembly.motor_rpm  = unpack_u16(f->data + 1);
        rx_assembly.torque     = f->data[3];
        rx_assembly.throttle   = f->data[4];
        rx_assembly.lat_accel  = unpack_i16(f->data + 5);
        break;
    case 3:  /* 0x213: 电压 + 电流 + 纵向加速度(有符号 i16，[4:5]) */
        rx_assembly.voltage     = unpack_u16(f->data + 0);
        rx_assembly.current     = unpack_u16(f->data + 2);
        rx_assembly.longi_accel = unpack_i16(f->data + 4);
        break;
    case 4:  /* 0x214: heading + U34 tick（U34 时钟不采用，统一用 F4 时间基准） */
        rx_assembly.heading = unpack_float(f->data + 0);
        break;
    case 5:  /* 0x215: gps_valid + seq */
        rx_assembly.gps_valid = f->data[0];
        break;
    }

    rx_frame_mask |= (1 << idx);/*1 << idx  把 1 移到第 idx 位，生成一个"只查这一帧"的掩码 */

    /* 6 帧到齐 → 推入 q_nav_result */
    if (rx_frame_mask == 0x3F) {
        rx_assembly.tick = HAL_GetTick();  /* 统一 F4 时间基准：完整 EKF 结果接收完成时刻 */
        nav_result_t *p = &nav_pool[nav_pool_wr % NAV_POOL_SIZE];
        *p = rx_assembly;
        nav_pool_wr++;

        /* 先排空旧数据再放入最新，保证 task_tx_pack 永远取最新 */
        nav_result_t *dummy;
        osMessageQueueGet(q_nav_resultHandle, &dummy, NULL, 0);/*非阻塞取，丢弃旧数据队列减少1*/
        osMessageQueuePut(q_nav_resultHandle, &p, 0, 0);

        rx_frame_mask = 0;  /* 复位，等待下一组 */
    }
}

/* ================================================================
 * 任务入口
 * ================================================================ */
void StartTask_can2(void *argument)
{
    can_frame_t frame;

    for (;;) {
        if (osMessageQueueGet(q_can2_rxHandle, &frame, NULL, osWaitForever) != osOK)
            continue;

        process_ekf_frame(&frame);
    }
}
