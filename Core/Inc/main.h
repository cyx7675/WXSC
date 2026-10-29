/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
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

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32f4xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "stm32f4xx_hal_iwdg.h"   // 提供 IWDG_HandleTypeDef
/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */
#define ADC_CHANNEL_NUM 10  // ADC 通道数量（与 app_types.h 的 ADC_CH_COUNT 一致）
/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */
extern uint16_t adc_dma_buf[ADC_CHANNEL_NUM];   // ADC DMA 缓冲区
extern  uint8_t  gps_dma_buf[1024];             // GPS DMA 缓冲区（字节流，须 >= GPS_PDU_SIZE）
/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define tuoluoyi tuoluoyi
#define CARcanRX_Pin GPIO_PIN_12
#define CARcanRX_GPIO_Port GPIOB
#define CARcanTX_Pin GPIO_PIN_13
#define CARcanTX_GPIO_Port GPIOB
#define GPSurtTX_Pin GPIO_PIN_9
#define GPSurtTX_GPIO_Port GPIOA
#define GPSurtRX_Pin GPIO_PIN_10
#define GPSurtRX_GPIO_Port GPIOA
#define FPUcanRX_Pin GPIO_PIN_11
#define FPUcanRX_GPIO_Port GPIOA
#define FPUcanTX_Pin GPIO_PIN_12
#define FPUcanTX_GPIO_Port GPIOA
#define WIFIurtTX_Pin GPIO_PIN_5
#define WIFIurtTX_GPIO_Port GPIOD
#define WIFIurtRX_Pin GPIO_PIN_6
#define WIFIurtRX_GPIO_Port GPIOD

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
