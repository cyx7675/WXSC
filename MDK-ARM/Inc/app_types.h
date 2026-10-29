/**
 * app_types.h — 全局类型定义
 * STM32F407VET6 / FreeRTOS / CubeMX
 */
#ifndef APP_TYPES_H
#define APP_TYPES_H

#include <stdint.h>
/* 通道在 adc_dma_buf[] 里的 0 基下标（HAL 的 DMA 按 Rank 顺序从 buf[0] 开始写，
 * Rank1→buf[0] … Rank10→buf[9]）。原先写成 1..10 会导致整体错位且
 * ADC_CH_PRES_3 越界访问 buf[10]。 */
#define ADC_CH_DISP_1     0   /* 线位移 1 */
#define ADC_CH_DISP_2     1   /* 线位移 2 */
#define ADC_CH_DISP_3     2   /* 线位移 3 */
#define ADC_CH_DISP_4     3   /* 线位移 4 */
#define ADC_CH_DISP_5     4   /* 线位移 5 */
#define ADC_CH_ANG_0      5   /* 角位移 */
#define ADC_CH_PRES_0     6   /* 压力 0 */
#define ADC_CH_PRES_1     7   /* 压力 1 */
#define ADC_CH_PRES_2     8   /* 压力 2 */
#define ADC_CH_PRES_3     9   /* 压力 3 */
/* ================================================================
 * CAN 帧（CAN1 陀螺仪 / CAN2）
 * ================================================================ */
typedef struct {
    uint32_t id;            /* CAN ID（标准帧 11bit 或扩展帧 29bit） */
    uint8_t  data[8];       /* 数据载荷 */
    uint8_t  dlc;           /* 数据长度 0~8 */
    uint32_t tick;          /* 接收时刻 HAL_GetTick() */
} can_frame_t;

/* ================================================================
 * IMU 解析结果（CAN1 陀螺仪，0x55 0x51/0x52 协议解算）
 * ================================================================ */

/* app_types.h */
typedef struct {
    float accel[3];     // m/s²  从 0x51 帧解析 加速度（量程 ±16g）
    float gyro[3];      // °/s   从 0x52 帧解析 角速度（量程 ±2000°/s）

    uint32_t tick;      // 接收时间戳
    uint8_t  updated;   // bit0=accel帧已收 bit1=gyro帧已收
} imu_data_t;
/* ================================================================
 * GPS 解析结果（NMEA / UBX）
 * ================================================================ */
typedef struct {
    double lat, lon;        /* 经纬度 度 */
    float  speed;           /* 地面速度 m/s */
    float  heading;         /* 航向角 度 */
    float  alt;             /* 海拔 m */
    uint8_t fix_quality;    /* 0=无定位,1=单点,2=DGPS,4=RTK固定,5=RTK浮点 */
    uint8_t sats;           /* 可见卫星数 */
    uint32_t tick;
} gps_data_t;

/* GPS 原始 PDU（UART2 DMA 缓冲 + IDLE 中断） */
#define GPS_PDU_SIZE 1024   /* 10Hz 下 NMEA 突发常 300~800B，必须 > 单突发长度，否则 RMC 被 128B 硬切导致 speed 整句丢失 → 0x122 速度冻结 */
typedef struct {
    uint8_t buf[GPS_PDU_SIZE];
    uint16_t len;
} gps_pdu_t;

/* ================================================================
 * 惯导输出（task_nav → q_nav_result → task_tx_pack）
 * ================================================================ */
typedef struct {
    float roll, pitch, yaw;     /* 欧拉角 度 */
    float speed, heading;       /* 融合后速度/航向 */

    /* --- 车辆/电机数据（U34 经 CAN2 0x212/0x213 回传，替代原 lat/lon 两帧） ---
     * 全部按原始码值透传（raw），量程/定标由 U34 与上位机约定 */
    uint8_t  motor_temp;        /* 电机温度   1B */
    uint16_t motor_rpm;         /* 电机转速   2B */
    uint8_t  torque;            /* 扭矩       1B */
    uint8_t  throttle;          /* 油门开度   1B */
    uint16_t voltage;           /* 电压       2B */
    uint16_t current;           /* 电流       2B */
    int16_t  lat_accel;         /* 横向加速度 2B 有符号（来自 0x212[5:6]，可为负） */
    int16_t  longi_accel;       /* 纵向加速度 2B 有符号（来自 0x213[4:5]，可为负） */

    uint32_t tick;              /* F4 接收完成时刻 HAL_GetTick()（统一 F4 基准，不用 U34 时钟） */
    uint8_t gps_valid;          /* 本周期是否注入 GPS */
} nav_result_t;



/* ================================================================
 * ADC 数据（10 通道，TIM3 触发 200Hz）
 * 直接发送原始 ADC 计数值（0~4095），**不做物理量换算**。
 * 量程/定标放在上位机/PC 端做，F4 只负责搬运原始采样。
 * 12bit 打包 10×12=120bit=15B，恰好无损（原始值必 < 4096）。
 * ================================================================ */
#define ADC_CH_COUNT 10
typedef struct {
    uint16_t raw[ADC_CH_COUNT];   /* 原始 ADC 计数值 0~4095，直接发送 */
    uint32_t tick;                /* 采样时刻 HAL_GetTick() */
} adc_data_t;
/* ================================================================
 * 无线发送包（USART2 → E34 发出），拆分为三个独立包
 *   - ADC   包：200Hz，27 字节（12bit 打包，适配 E34 单帧 ≤29B）
 *   - NAV   包： 25Hz，22 字节（姿态 + 横向/纵向加速度）
 *   - MOTOR 包： 50Hz，23 字节（车辆/电机数据 raw + 车速）
 *
 * ★ 字段归属原则：**一起用的放一起，同包 = 同时间戳**。
 *   打滑判断需要「轮速(motor_rpm) + 车速(speed)」同源同率，故都放在 50Hz 的
 *   MOTOR 包里；加速度是慢变量、且只作辅助判据，放到 25Hz 的 NAV 包。
 *   这样 MOTOR 50Hz 完整保留（瞬态不丢），NAV 降到 25Hz 省带宽。
 * 包类型由 type 字段区分：0=ADC, 1=NAV, 2=MOTOR
 *
 * ⚠ 每包必须 ≤29 字节：E34 半双工模式0 会自动按 29B 分包，
 *   超出就会产生一个几乎空的尾包，空中开销翻倍。
 *
 * 压包说明：
 *   ADC   — 10 路原始 ADC 值 0~4095，12bit 打包 10×12=120bit=15B，无损
 *           （原始值必 < 4096，比物理量换算更稳，绝不会因量程溢出截断）
 *   NAV   — 姿态用 int16×100 量化（0.01° 精度）；横向/纵向加速度 raw 有符号
 *   MOTOR — 电机量 raw 透传（U34 定标）；车速用 int16×100 量化（0.01 m/s）
 * ================================================================ */
#pragma pack(1)
typedef struct {
    uint16_t header;                        /* 0xAA55 */
    uint8_t  type;                          /* 包类型: 0=ADC */
    uint8_t  seq;                           /* 包序号 */
    uint32_t timestamp;                     /* ms 时间戳 */
    uint8_t  adc_packed[15];                /* 10 路 ADC 值，12bit 打包 = 120bit */
    uint16_t crc16;                         /* CRC-16/MODBUS（header 到 adc_packed） */
    uint16_t tail;                          /* 0x55AA */
} tx_adc_packet_t;                          /* = 27 字节 */

typedef struct {
    uint16_t header;                        /* 0xAA55 */
    uint8_t  type;                          /* 包类型: 1=NAV */
    uint8_t  seq;                           /* 包序号 */
    uint32_t timestamp;                     /* ms 时间戳（F4 接收完成时刻，统一 F4 基准） */
    int16_t  roll, pitch, yaw;              /* 姿态角 ×100，0.01° 精度，±327.67° */
    int16_t  lat_accel;                     /* 横向加速度 raw，有符号（U34 定标），25Hz */
    int16_t  longi_accel;                   /* 纵向加速度 raw，有符号（U34 定标），25Hz */
    uint16_t crc16;                         /* CRC-16/MODBUS（header 到 longi_accel） */
    uint16_t tail;                          /* 0x55AA */
} tx_nav_packet_t;                          /* = 22 字节，25Hz（原 28B/50Hz） */

/* MOTOR 包（车辆/电机数据，50Hz，23 字节）
 * 为什么不并进 NAV 包：NAV 去掉 lat/lon 后是 20B，再加电机 11B = 31B，
 * 超过 E34 半双工模式单帧 29B 上限，会被拆成 29+2 两个空包，空中开销翻倍。
 * 拆成独立包后两个都 ≤29B。 */
typedef struct {
    uint16_t header;                        /* 0xAA55 */
    uint8_t  type;                          /* 包类型: 2=MOTOR */
    uint8_t  seq;                           /* 包序号 */
    uint32_t timestamp;                     /* ms 时间戳（与同组 NAV 包相同） */
    uint8_t  motor_temp;                    /* 电机温度   raw */
    uint16_t motor_rpm;                     /* 电机转速   raw */
    uint8_t  torque;                        /* 扭矩       raw */
    uint8_t  throttle;                      /* 油门开度   raw */
    uint16_t voltage;                       /* 电压       raw */
    uint16_t current;                       /* 电流       raw */
    int16_t  speed;                         /* 车速 ×100，0.01 m/s —— 与 motor_rpm 同包同
                                             * 时间戳，上位机可直接算滑移率判断打滑 */
    uint16_t crc16;                         /* CRC-16/MODBUS（header 到 speed） */
    uint16_t tail;                          /* 0x55AA */
} tx_motor_packet_t;                        /* = 23 字节，50Hz */
#pragma pack()
/* ================================================================
 * EventGroup 位定义
 * ================================================================ */
#define EVT_IMU_READY   (1 << 0)
#define EVT_GPS_READY   (1 << 1)
#define EVT_ADC_READY   (1 << 2)    /* task_adc → task_tx_pack：ADC 数据就绪 */

/* ================================================================
 * CAN2 协议帧 ID（F4 ↔ U34 ECU）
 *
 * F4 角色：传感器前端，收 IMU(CAN1) + GPS(UART2) → 打包发 CAN2 → U34
 * U34 角色：运行 EKF（Simulink），结果通过 CAN2 回传 → F4 串口发数传
 *
 * 帧布局：标准帧 11bit ID，DLC=8，小端序（与 STM32 一致）
 * ================================================================ */

/* F4 → U34：IMU 数据（200Hz，4 帧/组）
 *   accel[3] + gyro[3] = 24 字节 + tick 4 字节 = 28 字节 → 4 × 8 = 32 字节 */
#define CAN2_ID_IMU_0    0x110   /* [0:3] accel[0](float)X轴加速度 [4:7] accel[1](float)y轴加速度 */
#define CAN2_ID_IMU_1    0x111   /* [0:3] accel[2](float)z轴加速度 [4:7] gyro[0](float)X轴角速度  */
#define CAN2_ID_IMU_2    0x112   /* [0:3] gyro[1](float)Y轴角速度  [4:7] gyro[2](float)Z轴角速度  */
#define CAN2_ID_IMU_3    0x113   /* [0:3] tick(u32)  [4] seq [5] valid [6:7] rsv */

/* F4 → U34：GPS 数据（10Hz，5 帧/组） */
#define CAN2_ID_GPS_0    0x120   /* [0:7] lat(double, 8字节)            */
#define CAN2_ID_GPS_1    0x121   /* [0:7] lon(double, 8字节)            */
#define CAN2_ID_GPS_2    0x122   /* [0:3] speed(float) [4:7] heading(float) */
#define CAN2_ID_GPS_3    0x123   /* [0:3] alt(float)海拔 [4] fix定位质量（0=无定位，1=GPS，2=DGPS） [5] sats可见卫星数[6:7] rsv */
#define CAN2_ID_GPS_4    0x124   /* [0:3] tick(u32) [4] seq [5] valid [6:7] rsv */

/* U34 → F4：EKF 结果（50Hz，6 帧/组） */
#define CAN2_ID_EKF_0    0x210   /* [0:3] roll(float)横滚角  [4:7] pitch(float)俯仰角  */
#define CAN2_ID_EKF_1    0x211   /* [0:3] yaw(float)航向角   [4:7] speed(float)   */
/* 车辆/电机帧（替代原 lat/lon 两帧，2026-09-29 改） */
#define CAN2_ID_EKF_2    0x212   /* [0] 电机温度u8  [1:2] 电机转速u16  [3] 扭矩u8  [4] 油门开度u8  [5:6] 加速度i16(有符号)  [7] rsv */
#define CAN2_ID_EKF_3    0x213   /* [0:1] 电压u16   [2:3] 电流u16      [4:5] 纵向加速度i16(有符号)   [6:7] rsv           */
#define CAN2_ID_EKF_4    0x214   /* [0:3] heading(float) [4:7] tick(u32)   */
#define CAN2_ID_EKF_5    0x215   /* [0] gps_valid [1] GPS是否有效（1=有定位，0=飘了）seq [2:7] rsv 序列号       */







#define CAN2_EKF_FRAME_COUNT  6   /* U34→F4 EKF 结果总帧数 */

#endif /* APP_TYPES_H */
