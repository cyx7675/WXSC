/**
 * task_adc.c — ADC 数据读取任务
 *
 * ADC1 由 TIM3 TRGO 硬件触发，DMA2 Stream0 环形搬运 10 通道原始计数值。
 * 本任务每 5ms 读一次 DMA 缓冲，可选 EMA 滤波后写入 g_adc_data.raw[]，
 * **直接发送原始 ADC 值（0~4095），不做物理量换算** —— 量程/定标放上位机做。
 *
 * 优先级 6（和 IMU/GPS 同级，时间片轮转）
 */

#include "app_types.h"
#include "app_queues.h"
#include "main.h"
#include "cmsis_os2.h"
#include <string.h>
#include "task_adc.h"

/* ---- ADC DMA 缓冲（在 main.c 中定义） ---- */
extern uint16_t adc_dma_buf[ADC_CH_COUNT];

/* ---- 共享 ADC 数据 ---- */
adc_data_t g_adc_data;

/* ---- 指数滑动平均(EMA)滤波 ----
 * 旧写法 ema += (val-ema)>>3 有致命整数截断死区。现改用 Q4 定点累加器。
 *
 * ADC_EMA_SHIFT 调节强度（0 = 完全关闭，直通原始值，零延迟）：
 *   0  直通       原始值直发（默认，符合"直接发送原始值"）
 *   1  alpha 1/2  约 2 个样本 (10ms)
 *   2  alpha 1/4  约 4 个样本 (20ms)
 *   3  alpha 1/8  约 8 个样本 (40ms)
 * 采样时间已按源阻抗加长(见 adc.c)，原始值本身已很干净，故默认取直通。
 * 注意：滤波作用于原始计数值（0~4095），不是物理量。 */
#define ADC_EMA_SHIFT  0       /* ← 改 1/2/3 开启平滑 */
#define ADC_EMA_Q      4
static int32_t ema_raw[ADC_CH_COUNT];
static uint8_t  ema_ready = 0;

/* 滤波更新：Q4 定点累加 + 舍入输出，返回滤波后的整数值。
 * SHIFT=0 时直接返回 sample（直通，零延迟、零损失）。 */
static uint16_t ema_update(int32_t *st, uint16_t sample)
{
    int32_t target = (int32_t)sample << ADC_EMA_Q;
    if (!ema_ready || ADC_EMA_SHIFT == 0)
        *st = target;                               /* 首轮取初值 / 关闭滤波：直通 */
    else
        *st += (target - *st) >> ADC_EMA_SHIFT;     /* 定点 EMA，无死区 */
    return (uint16_t)((*st + (1 << (ADC_EMA_Q - 1))) >> ADC_EMA_Q);
}

/* ================================================================
 * 任务入口
 * ================================================================ */
void StartTask_adc(void *argument)
{
    for (;;) {
        /* 5ms 周期（TIM3 触发 200Hz，DMA 持续覆盖） */
        osDelay(5);

        /* 抢锁 → 读 DMA → 可选滤波 → 放锁 */
        if (osMutexAcquire(myMutex_adcHandle, osWaitForever) != osOK)
            continue;

        /* 直接搬运 10 路原始计数值（0~4095），可选 EMA 平滑 */
        for (int i = 0; i < ADC_CH_COUNT; i++) {
            g_adc_data.raw[i] = ema_update(&ema_raw[i], adc_dma_buf[i]);
        }
        ema_ready = 1;   /* 首轮完成初值，之后进入平滑 */
        g_adc_data.tick = HAL_GetTick();

        osMutexRelease(myMutex_adcHandle);

        /* 通知 task_tx_pack：ADC 数据就绪 */
        osEventFlagsSet(eg_sensorHandle, EVT_ADC_READY);
    }
}
