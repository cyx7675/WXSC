/**
 * app_queues.h — 队列/信号量/事件组句柄声明
 * 句柄在 CubeMX 的 freertos.c 中定义，这里 extern 供全局使用
 * 统一使用 CMSIS_OS2 API（与 CubeMX 生成的一致）
 */
#ifndef APP_QUEUES_H
#define APP_QUEUES_H

#include "cmsis_os2.h"

/* ---- 队 列 ---- */
extern osMessageQueueId_t q_imu_rawHandle;     /* can_frame_t, 传值, 深度 16 */
extern osMessageQueueId_t q_can2_rxHandle;     /* can_frame_t, 传值, 深度 8  (U34 EKF 结果帧) */
extern osMessageQueueId_t q_gps_rawHandle;     /* gps_pdu_t*, 传指针, 深度 4  */
extern osMessageQueueId_t q_nav_resultHandle;  /* nav_result_t*, 传指针, 深度 4 (U34 EKF 回传) */

/* ---- 互斥锁 ---- */
extern osMutexId_t myMutex_adcHandle;
extern osMutexId_t myMutex_nav_resultHandle;

/* ---- 事件组 ---- */
extern osEventFlagsId_t eg_sensorHandle;  /* bit0=IMU_READY, bit1=GPS_READY, bit2=ADC_READY */

/* ---- 信号量 ---- */
extern osSemaphoreId_t sem_tx_doneHandle; /* 用于通知发送完成 */

#endif /* APP_QUEUES_H */
