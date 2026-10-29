/**
 * task_monitor.c — 系统监控（IWDG 喂狗 + 栈水位 + 运行指示灯）
 *
 * 优先级 2（最低），100ms 周期
 * 如果高优先级任务卡死，本任务得不到 CPU，看门狗超时复位
 */

#include "app_types.h"
#include "app_queues.h"
#include "main.h"
#include "iwdg.h"
#include "FreeRTOS.h"
#include "task.h"
#include "cmsis_os2.h"
#include <stdio.h>

/* ---- 调试输出（SEGGER RTT 或 ITM），条件编译 ---- */
#define MONITOR_PRINTF(...)  /* 默认关闭，需要时改为 printf(__VA_ARGS__) */

/* ================================================================
 * LED 心跳（PC12 ）
 * ================================================================ */
static void toggle_led(void)
{
    HAL_GPIO_TogglePin(GPIOC, GPIO_PIN_12);  /* 板载 LED */
}

/* ================================================================
 * 任务入口
 * ================================================================ */
void StartTask_monitor(void *argument)
{
    TickType_t last_wake = xTaskGetTickCount();
    UBaseType_t stack_high;

    for (;;) {
        /* 100ms 周期 */
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(100));

        /* ---- IWDG 喂狗 ---- */
        HAL_IWDG_Refresh(&hiwdg);

        /* ---- LED 心跳 ---- */
        toggle_led();

        /* ---- 栈水位检查（最低剩余空间） ---- */
        stack_high = uxTaskGetStackHighWaterMark(NULL);
        if (stack_high < 64) {
            /* 栈即将溢出，强制触发看门狗复位 */
            MONITOR_PRINTF("ALERT: stack low %lu\n", stack_high);
            while (1) __NOP(); /* 停止喂狗，等待复位 */
        }

        /* ---- 可选：运行时统计 ---- */
        #if 0
        TaskStatus_t pxTaskStatusArray[6];
        unsigned long ulTotalTime;
        volatile UBaseType_t uxArraySize = 6;
        uxTaskGetSystemState(pxTaskStatusArray, uxArraySize, &ulTotalTime);
        #endif
    }
}
