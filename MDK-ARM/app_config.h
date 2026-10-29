#ifndef APP_CONFIG_H
#define APP_CONFIG_H

/* ---- 任务栈大小（单位：word = 4字节）---- */
#define STACK_NAV       1024   /* 惯导算法，有矩阵运算，栈要大 */
#define STACK_IMU_RX    512
#define STACK_GPS_RX    512
#define STACK_ADC       512
#define STACK_CAN2      512
#define STACK_TX_PACK   512
#define STACK_MONITOR   256

/* ---- 任务周期 ---- */
#define PERIOD_ADC_MS       5
#define PERIOD_NAV_MS       10   /* 惯导输出 100Hz */
#define PERIOD_TX_MS        20   /* 打包发送 50Hz */

/* ---- 队列深度 ---- */
#define Q_IMU_DEPTH     8
#define Q_GPS_DEPTH     4
#define Q_ADC_DEPTH     4
#define Q_CAN2_RX_DEPTH 16
#define Q_TX_DEPTH      8

/* ---- 系统事件位 ---- */
#define EVT_IMU_READY       (1 << 0)
#define EVT_GPS_READY       (1 << 1)
#define EVT_ADC_READY       (1 << 2)
#define EVT_NAV_DONE        (1 << 3)
#define EVT_ALIVE_MASK      (0xFF << 8)  /* 各任务心跳位 */

#endif