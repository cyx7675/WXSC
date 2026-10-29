/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * File Name          : freertos.c
  * Description        : Code for freertos applications
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "FreeRTOS.h"
#include "task.h"
#include "main.h"
#include "cmsis_os.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "app_types.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
typedef StaticQueue_t osStaticMessageQDef_t;
typedef StaticSemaphore_t osStaticMutexDef_t;
typedef StaticEventGroup_t osStaticEventGroupDef_t;
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN Variables */

/* USER CODE END Variables */
/* Definitions for StartTask */
osThreadId_t StartTaskHandle;
const osThreadAttr_t StartTask_attributes = {
  .name = "StartTask",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityAboveNormal,
};
/* Definitions for task_nav */
osThreadId_t task_navHandle;
const osThreadAttr_t task_nav_attributes = {
  .name = "task_nav",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityHigh,
};
/* Definitions for task_imu_rx */
osThreadId_t task_imu_rxHandle;
const osThreadAttr_t task_imu_rx_attributes = {
  .name = "task_imu_rx",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityAboveNormal,
};
/* Definitions for task_gps_rx */
osThreadId_t task_gps_rxHandle;
const osThreadAttr_t task_gps_rx_attributes = {
  .name = "task_gps_rx",
  .stack_size = 256 * 4,   /* 1024B：atof() 库实现栈 ~300-500B，原 512B 解析 NMEA 调用 atof 会撑爆栈 → HardFault → IWDG 1.25s 复位 */
  .priority = (osPriority_t) osPriorityAboveNormal,
};
/* Definitions for task_can2 */
osThreadId_t task_can2Handle;
const osThreadAttr_t task_can2_attributes = {
  .name = "task_can2",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityNormal,
};
/* Definitions for task_tx_pack */
osThreadId_t task_tx_packHandle;
const osThreadAttr_t task_tx_pack_attributes = {
  .name = "task_tx_pack",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityBelowNormal,
};
/* Definitions for task_monitor */
osThreadId_t task_monitorHandle;
const osThreadAttr_t task_monitor_attributes = {
  .name = "task_monitor",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
/* Definitions for task_adc */
osThreadId_t task_adcHandle;
const osThreadAttr_t task_adc_attributes = {
  .name = "task_adc",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityAboveNormal,
};
/* Definitions for q_imu_raw */
osMessageQueueId_t q_imu_rawHandle;
uint8_t q_imu_rawBuffer[ 16 * sizeof( can_frame_t ) ];
osStaticMessageQDef_t q_imu_rawControlBlock;
const osMessageQueueAttr_t q_imu_raw_attributes = {
  .name = "q_imu_raw",
  .cb_mem = &q_imu_rawControlBlock,
  .cb_size = sizeof(q_imu_rawControlBlock),
  .mq_mem = &q_imu_rawBuffer,
  .mq_size = sizeof(q_imu_rawBuffer)
};
/* Definitions for q_can2_rx */
osMessageQueueId_t q_can2_rxHandle;
uint8_t q_can2_rxBuffer[ 8 * sizeof( can_frame_t ) ];
osStaticMessageQDef_t q_can2_rxControlBlock;
const osMessageQueueAttr_t q_can2_rx_attributes = {
  .name = "q_can2_rx",
  .cb_mem = &q_can2_rxControlBlock,
  .cb_size = sizeof(q_can2_rxControlBlock),
  .mq_mem = &q_can2_rxBuffer,
  .mq_size = sizeof(q_can2_rxBuffer)
};
/* Definitions for q_gps_raw */
osMessageQueueId_t q_gps_rawHandle;
uint8_t q_gps_rawBuffer[ 4 * sizeof( gps_pdu_t* ) ];
osStaticMessageQDef_t q_gps_rawControlBlock;
const osMessageQueueAttr_t q_gps_raw_attributes = {
  .name = "q_gps_raw",
  .cb_mem = &q_gps_rawControlBlock,
  .cb_size = sizeof(q_gps_rawControlBlock),
  .mq_mem = &q_gps_rawBuffer,
  .mq_size = sizeof(q_gps_rawBuffer)
};
/* Definitions for q_nav_result */
osMessageQueueId_t q_nav_resultHandle;
uint8_t q_nav_resultBuffer[ 4 * sizeof( nav_result_t* ) ];
osStaticMessageQDef_t q_nav_resultControlBlock;
const osMessageQueueAttr_t q_nav_result_attributes = {
  .name = "q_nav_result",
  .cb_mem = &q_nav_resultControlBlock,
  .cb_size = sizeof(q_nav_resultControlBlock),
  .mq_mem = &q_nav_resultBuffer,
  .mq_size = sizeof(q_nav_resultBuffer)
};
/* Definitions for myMutex_adc */
osMutexId_t myMutex_adcHandle;
osStaticMutexDef_t myMutex_adcControlBlock;
const osMutexAttr_t myMutex_adc_attributes = {
  .name = "myMutex_adc",
  .cb_mem = &myMutex_adcControlBlock,
  .cb_size = sizeof(myMutex_adcControlBlock),
};
/* Definitions for myMutex_nav_result */
osMutexId_t myMutex_nav_resultHandle;
osStaticMutexDef_t myMutex_nav_resultControlBlock;
const osMutexAttr_t myMutex_nav_result_attributes = {
  .name = "myMutex_nav_result",
  .cb_mem = &myMutex_nav_resultControlBlock,
  .cb_size = sizeof(myMutex_nav_resultControlBlock),
};
/* Definitions for sem_tx_done */
osSemaphoreId_t sem_tx_doneHandle;
const osSemaphoreAttr_t sem_tx_done_attributes = {
  .name = "sem_tx_done"
};
/* Definitions for eg_sensor */
osEventFlagsId_t eg_sensorHandle;
osStaticEventGroupDef_t evt_imuControlBlock;
const osEventFlagsAttr_t eg_sensor_attributes = {
  .name = "eg_sensor",
  .cb_mem = &evt_imuControlBlock,
  .cb_size = sizeof(evt_imuControlBlock),
};

/* Private function prototypes -----------------------------------------------*/
/* USER CODE BEGIN FunctionPrototypes */

/* USER CODE END FunctionPrototypes */

void StartTask_01(void *argument);
extern void StartTask_nav(void *argument);
extern void StartTask_imu_rx(void *argument);
extern void StartTask_gps_rx(void *argument);
extern void StartTask_can2(void *argument);
extern void StartTask_tx_pack(void *argument);
extern void StartTask_monitor(void *argument);
extern void StartTask_adc(void *argument);

void MX_FREERTOS_Init(void); /* (MISRA C 2004 rule 8.1) */

/* Hook prototypes */
void vApplicationMallocFailedHook(void);

/* USER CODE BEGIN 5 */
void vApplicationMallocFailedHook(void)
{
   /* vApplicationMallocFailedHook() will only be called if
   configUSE_MALLOC_FAILED_HOOK is set to 1 in FreeRTOSConfig.h. It is a hook
   function that will get called if a call to pvPortMalloc() fails.
   pvPortMalloc() is called internally by the kernel whenever a task, queue,
   timer or semaphore is created. It is also called by various parts of the
   demo application. If heap_1.c or heap_2.c are used, then the size of the
   heap available to pvPortMalloc() is defined by configTOTAL_HEAP_SIZE in
   FreeRTOSConfig.h, and the xPortGetFreeHeapSize() API function can be used
   to query the size of free heap space that remains (although it does not
   provide information on how the remaining heap might be fragmented). */
}
/* USER CODE END 5 */

/**
  * @brief  FreeRTOS initialization
  * @param  None
  * @retval None
  */
void MX_FREERTOS_Init(void) {
  /* USER CODE BEGIN Init */

  /* USER CODE END Init */
  /* Create the mutex(es) */
  /* creation of myMutex_adc */
  myMutex_adcHandle = osMutexNew(&myMutex_adc_attributes);

  /* creation of myMutex_nav_result */
  myMutex_nav_resultHandle = osMutexNew(&myMutex_nav_result_attributes);

  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* Create the semaphores(s) */
  /* creation of sem_tx_done */
  sem_tx_doneHandle = osSemaphoreNew(1, 1, &sem_tx_done_attributes);

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* Create the queue(s) */
  /* creation of q_imu_raw */
  q_imu_rawHandle = osMessageQueueNew (16, sizeof(can_frame_t), &q_imu_raw_attributes);

  /* creation of q_can2_rx */
  q_can2_rxHandle = osMessageQueueNew (8, sizeof(can_frame_t), &q_can2_rx_attributes);

  /* creation of q_gps_raw */
  q_gps_rawHandle = osMessageQueueNew (4, sizeof(gps_pdu_t*), &q_gps_raw_attributes);

  /* creation of q_nav_result */
  q_nav_resultHandle = osMessageQueueNew (4, sizeof(nav_result_t*), &q_nav_result_attributes);

  /* USER CODE BEGIN RTOS_QUEUES */
  /* add queues, ... */
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of StartTask */
  StartTaskHandle = osThreadNew(StartTask_01, NULL, &StartTask_attributes);

  /* creation of task_nav */
  task_navHandle = osThreadNew(StartTask_nav, NULL, &task_nav_attributes);

  /* creation of task_imu_rx */
  task_imu_rxHandle = osThreadNew(StartTask_imu_rx, NULL, &task_imu_rx_attributes);

  /* creation of task_gps_rx */
  task_gps_rxHandle = osThreadNew(StartTask_gps_rx, NULL, &task_gps_rx_attributes);

  /* creation of task_can2 */
  task_can2Handle = osThreadNew(StartTask_can2, NULL, &task_can2_attributes);

  /* creation of task_tx_pack */
  task_tx_packHandle = osThreadNew(StartTask_tx_pack, NULL, &task_tx_pack_attributes);

  /* creation of task_monitor */
  task_monitorHandle = osThreadNew(StartTask_monitor, NULL, &task_monitor_attributes);

  /* creation of task_adc */
  task_adcHandle = osThreadNew(StartTask_adc, NULL, &task_adc_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  /* add threads, ... */
  /* USER CODE END RTOS_THREADS */

  /* creation of eg_sensor */
  eg_sensorHandle = osEventFlagsNew(&eg_sensor_attributes);

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

}

/* USER CODE BEGIN Header_StartTask_01 */
/**
  * @brief  Function implementing the StartTask thread.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_StartTask_01 */
void StartTask_01(void *argument)
{
  /* USER CODE BEGIN StartTask_01 */
  /* Infinite loop */
  for(;;)
  {
    osDelay(1);
  }
  /* USER CODE END StartTask_01 */
}

/* Private application code --------------------------------------------------*/
/* USER CODE BEGIN Application */

/* USER CODE END Application */

