/**
 * task_imu_rx.c — CAN1 陀螺仪数据接收/解析（0x55 0x51/0x52 协议）
 *
 * IMU 报文格式（CAN1，500kbps，标准帧 ID = IMU_CAN_ID，DLC=8）：
 *   data[0] = 0x55        帧头（固定）
 *   data[1] = 0x51        加速度帧：data[2..7] = AxL AxH AyL AyH AzL AzH
 *   data[1] = 0x52        角速度帧：data[2..7] = WxL WxH WyL WyH WzL WzH
 *
 * 换算（有符号 16bit，小端：低字节在前）：
 *   accel (m/s²) = ((AxH<<8)|AxL) / 32768 × 16 × g,  g = 9.8
 *   gyro  (°/s)  = ((WxH<<8)|WxL) / 32768 × 2000
 *
 * 优先级 6，阻塞等待 q_imu_raw，解析后更新 g_imu_data + 置 EVT_IMU_READY
 */

#include "app_types.h"
#include "app_queues.h"
#include "cmsis_os2.h"
#include <string.h>

/* ---- 调试计数器（定义在 main.c，Keil Watch 窗口查看） ---- */
extern volatile uint32_t dbg_can1_id_ok, dbg_can1_hdr_ok;
extern volatile uint32_t dbg_accel_rx, dbg_gyro_rx, dbg_evt_imu_ready;

#define LE16S(b0, b1)  ((int16_t)((uint16_t)(b1) << 8 | (b0)))
/* LE = Little-Endian（小端序，低字节在前）
 * 16 = 16 位
 * S  = Signed（有符号） */

/* ---- IMU 协议常量 ---- */
#define IMU_CAN_ID       0x7F       /* IMU 报文 CAN ID（标准帧，用户确认此值可正常收发） */
#define IMU_HEADER       0x55       /* data[0] 帧头 */
#define IMU_TYPE_ACCEL   0x51       /* data[1] = 加速度帧 */
#define IMU_TYPE_GYRO    0x52       /* data[1] = 角速度帧 */

/* 每 LSB 物理量 = 量程 × 满值 / 32768 */
#define ACCEL_SCALE      (16.0f * 9.8f / 32768.0f)   /* ≈0.004785 m/s² per LSB */
#define GYRO_SCALE       (2000.0f / 32768.0f)        /* ≈0.061035 °/s per LSB  */

#define UPD_ACCEL        0x01
#define UPD_GYRO         0x02

/* ---- 共享数据（task_nav 只读 + task_imu_rx 只写，拿 myMutex_nav_resultHandle 保护） ---- */
imu_data_t g_imu_data;/* 使用全局变量原因，快速覆盖，不产生延时帧 */

/* ================================================================
 * 陀螺仪 CAN 帧解析
 * ================================================================ */
void StartTask_imu_rx(void *argument)
{
    can_frame_t frame;
    imu_data_t  accu = {0};   /* 累积缓冲，整个任务生命周期内复用 */

    for (;;) {
        if (osMessageQueueGet(q_imu_rawHandle, &frame, NULL, osWaitForever) != osOK)
            continue;

        /* 只收 IMU 的 ID，且帧头必须是 0x55 */
        if (frame.id != IMU_CAN_ID)
            continue;
        dbg_can1_id_ok++;
        if (frame.dlc < 8 || frame.data[0] != IMU_HEADER)
            continue;
        dbg_can1_hdr_ok++;

        osMutexAcquire(myMutex_nav_resultHandle, osWaitForever);

        switch (frame.data[1]) {
        case IMU_TYPE_ACCEL:   /* 0x51：加速度 X/Y/Z */
            accu.accel[0] = LE16S(frame.data[2], frame.data[3]) * ACCEL_SCALE;
            accu.accel[1] = LE16S(frame.data[4], frame.data[5]) * ACCEL_SCALE;
            accu.accel[2] = LE16S(frame.data[6], frame.data[7]) * ACCEL_SCALE;
            accu.updated |= UPD_ACCEL;
            dbg_accel_rx++;
            break;

        case IMU_TYPE_GYRO:    /* 0x52：角速度 X/Y/Z */
            accu.gyro[0] = LE16S(frame.data[2], frame.data[3]) * GYRO_SCALE;
            accu.gyro[1] = LE16S(frame.data[4], frame.data[5]) * GYRO_SCALE;
            accu.gyro[2] = LE16S(frame.data[6], frame.data[7]) * GYRO_SCALE;
            accu.tick     = frame.tick;
            accu.updated |= UPD_GYRO;
            dbg_gyro_rx++;
            break;

        default: break;       /* 其他类型帧（温度/角度等）暂不解析 */
        }

        /* 加速度 + 角速度都到齐 → 推送 + 通知 */
        if ((accu.updated & (UPD_ACCEL | UPD_GYRO)) == (UPD_ACCEL | UPD_GYRO)) {
            memcpy(&g_imu_data, &accu, sizeof(imu_data_t));
            accu.updated = 0;   /* 清标志，准备下一组 */
            osEventFlagsSet(eg_sensorHandle, EVT_IMU_READY);
            dbg_evt_imu_ready++;
        }

        osMutexRelease(myMutex_nav_resultHandle);
    }
}
