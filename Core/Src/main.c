/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
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
#include "main.h"
#include "cmsis_os.h"
#include "adc.h"
#include "can.h"
#include "dma.h"
#include "iwdg.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"
#include "app_types.h"
#include "app_queues.h"
#include "string.h"
/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */


/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
uint16_t adc_dma_buf[ADC_CHANNEL_NUM];   // ADC DMA 缓冲区（10 通道）
uint8_t  gps_dma_buf[1024];              // GPS UART DMA 缓冲区（字节流，须 >= GPS_PDU_SIZE）
/* GPS PDU 环形缓冲池（供 IDLE 中断回调使用） */
#define GPS_POOL_SIZE 8   /* 放大 PDU 后单突发仅 1 个 PDU，10Hz=10PDU/s，8 深池防 ISR 回卷覆盖正在读取的 PDU */
static gps_pdu_t gps_pool[GPS_POOL_SIZE];
static uint8_t gps_pool_wr = 0;

/* ---- 调试计数器（Keil 调试器 Watch 窗口查看，定位 CAN2 不发数据） ---- */
volatile uint32_t dbg_can1_isr = 0;      /* CAN1 ISR 被调用次数（0=收不到数据） */
volatile uint32_t dbg_can1_id_ok = 0;    /* CAN1 ID==0x7F 匹配次数（0=ID不对） */
volatile uint32_t dbg_can1_hdr_ok = 0;   /* 帧头0x55+DLC>=8 匹配次数（0=协议不对） */
volatile uint32_t dbg_accel_rx = 0;      /* 收到0x51加速度帧次数 */
volatile uint32_t dbg_gyro_rx = 0;       /* 收到0x52角速度帧次数 */
volatile uint32_t dbg_evt_imu_ready = 0; /* EVT_IMU_READY 触发次数（0=accel+gyro没齐） */
volatile uint32_t dbg_nav_loop = 0;      /* task_nav 循环次数（0=EVT没触发） */
volatile uint32_t dbg_can2_tx = 0;       /* CAN2 发送尝试次数（0=task_nav没跑到） */
volatile uint32_t dbg_gps_rx = 0;       /* GPS UART 收到数据次数（0=UART2没数据/波特率不对） */
volatile uint32_t dbg_gps_parse = 0;    /* NMEA 校验通过次数（0=数据乱码/格式不对） */
volatile uint32_t dbg_gps_fix = 0;      /* GPS 定位成功次数（0=没搜到星） */
volatile uint32_t dbg_gps_evt = 0;      /* EVT_GPS_READY 触发次数 */
volatile uint32_t dbg_gps_idle = 0;     /* USART1(PA10, GPS) IDLE 回调触发次数（0=USART1没收到GPS数据） */
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
void MX_FREERTOS_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* 供中断回调调用的外部函数声明 */
extern void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan);
extern void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size);
extern void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart);
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
 {

  /* USER CODE BEGIN 1 */
  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_USART1_UART_Init();
  MX_USART2_UART_Init();
  MX_ADC1_Init();
  MX_CAN1_Init();
  MX_CAN2_Init();
  MX_IWDG_Init();
  MX_TIM3_Init();
  /* USER CODE BEGIN 2 */

/* 2. 启动 ADC DMA（Circular，TIM3 触发） */
HAL_ADC_Start_DMA(&hadc1, (uint32_t*)adc_dma_buf, ADC_CH_COUNT);

/* 3. 启动 TIM3（触发 ADC 采样） */
HAL_TIM_Base_Start(&htim3);

/* 3.5 CAN 过滤器配置（全通：mask=0 接收所有帧）
 *    STM32F4 上电后所有过滤器 bank 非激活 → 不配过滤器 CAN 不收任何帧 */
CAN_FilterTypeDef can_filter = {0};
can_filter.FilterMode = CAN_FILTERMODE_IDMASK;
can_filter.FilterScale = CAN_FILTERSCALE_32BIT;
can_filter.FilterIdHigh = 0x0000;
can_filter.FilterIdLow = 0x0000;
can_filter.FilterMaskIdHigh = 0x0000;   /* mask=0 → 不检查任何位 → 全通 */
can_filter.FilterMaskIdLow = 0x0000;
can_filter.FilterFIFOAssignment = CAN_FILTER_FIFO0;
can_filter.FilterActivation = ENABLE;
can_filter.SlaveStartFilterBank = 14;    /* CAN1 用 bank 0-13，CAN2 用 bank 14-27 */

can_filter.FilterBank = 0;
HAL_CAN_ConfigFilter(&hcan1, &can_filter);   /* CAN1 过滤器 */

can_filter.FilterBank = 14;
HAL_CAN_ConfigFilter(&hcan2, &can_filter);   /* CAN2 过滤器 */

/* 3.6 手动使能 CAN2 RX0 中断（CubeMX .ioc 未配 CAN2_RX0_IRQn，在此补上） */
HAL_NVIC_SetPriority(CAN2_RX0_IRQn, 5, 0);
HAL_NVIC_EnableIRQ(CAN2_RX0_IRQn);

/* 4. 启动 CAN1 + 开启接收中断 */
HAL_CAN_Start(&hcan1);
HAL_CAN_ActivateNotification(&hcan1, CAN_IT_RX_FIFO0_MSG_PENDING);

/* 5. 启动 CAN2 + 开启接收中断 */
HAL_CAN_Start(&hcan2);
HAL_CAN_ActivateNotification(&hcan2, CAN_IT_RX_FIFO0_MSG_PENDING);

/* 6. 启动 GPS UART DMA 接收（USART1/PA10，IDLE 检测） */
HAL_UARTEx_ReceiveToIdle_DMA(&huart1,  (uint8_t*)gps_dma_buf, GPS_PDU_SIZE);
__HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);  // 关半传输中断，只用IDLE

  /* USER CODE END 2 */

  /* Init scheduler */
  osKernelInitialize();  /* Call init function for freertos objects (in cmsis_os2.c) */
  MX_FREERTOS_Init();

  /* Start scheduler */
  osKernelStart();

  /* We should never get here as control is now taken by the scheduler */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_LSI|RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.LSIState = RCC_LSI_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 4;
  RCC_OscInitStruct.PLL.PLLN = 84;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }

  /** Enables the Clock Security System
  */
  HAL_RCC_EnableCSS();
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  Period elapsed callback in non blocking mode
  * @note   This function is called  when TIM7 interrupt took place, inside
  * HAL_TIM_IRQHandler(). It makes a direct call to HAL_IncTick() to increment
  * a global variable "uwTick" used as application time base.
  * @param  htim : TIM handle
  * @retval None
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  /* USER CODE BEGIN Callback 0 */

  /* USER CODE END Callback 0 */
  if (htim->Instance == TIM7)
  {
    HAL_IncTick();
  }
}
  /* USER CODE BEGIN Callback 1 */
/* ---- CAN1 接收回调 ---- */
void HAL_CAN_RxFifo0MsgPendingCallback(CAN_HandleTypeDef *hcan)
{
    if (hcan->Instance == CAN1) {
        CAN_RxHeaderTypeDef hdr;
        uint8_t data[8];
        HAL_CAN_GetRxMessage(hcan, CAN_RX_FIFO0, &hdr, data);
        dbg_can1_isr++;

        can_frame_t frame;
        frame.id   = (hdr.IDE == CAN_ID_STD) ? hdr.StdId : hdr.ExtId;
        frame.dlc  = hdr.DLC;
        frame.tick = HAL_GetTick();
        memcpy(frame.data, data, 8);

        osMessageQueuePut(q_imu_rawHandle, &frame, 0, 0);
    }
    else if (hcan->Instance == CAN2) {
        /* CAN2 同理，发到 q_can2_rx */
        CAN_RxHeaderTypeDef hdr;
        uint8_t data[8];
        HAL_CAN_GetRxMessage(hcan, CAN_RX_FIFO0, &hdr, data);

        can_frame_t frame;
        frame.id   = (hdr.IDE == CAN_ID_STD) ? hdr.StdId : hdr.ExtId;
        frame.dlc  = hdr.DLC;
        frame.tick = HAL_GetTick();
        memcpy(frame.data, data, 8);

        osMessageQueuePut(q_can2_rxHandle, &frame, 0, 0);
    }
}

/* ---- GPS UART IDLE 回调 ---- */
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance != USART1) return;   /* GPS 接在 USART1(PA10) */

    dbg_gps_idle++;   /* IDLE 触发了 → 说明 USART1 收到了 GPS 数据且 DMA+IDLE 正常 */

    gps_pdu_t *p = &gps_pool[gps_pool_wr % GPS_POOL_SIZE];
    /* 取环形缓冲池的下一个 PDU  % 是取模。gps_pool_wr 只增不减，除 4 取余数，实现无限循环的 0→1→2→3→0→1→2→3…：*/
    gps_pool_wr++;

    uint16_t n = (Size > GPS_PDU_SIZE) ? GPS_PDU_SIZE : Size;  /* 封顶到 GPS_PDU_SIZE，防单突发超长越界写穿 .bss → HardFault */
    memcpy(p->buf, gps_dma_buf, n);
    p->len = n;

    osMessageQueuePut(q_gps_rawHandle, &p, 0, 0);

    /* 重新启动 DMA 接收 */
    HAL_UARTEx_ReceiveToIdle_DMA(&huart1, gps_dma_buf, GPS_PDU_SIZE);
    __HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);
}

/* ---- E34 数传(UART2/PD5) DMA 发送完成回调 ---- */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2) {
        /* 通知 task_tx_pack 可以发下一包 */
        osSemaphoreRelease(sem_tx_doneHandle);
    }
}

  /* USER CODE END Callback 1 */


/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}



/* USER CODE END 4 */
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
