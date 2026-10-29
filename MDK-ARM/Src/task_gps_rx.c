/**
 * task_gps_rx.c — GPS NMEA 协议解析
 * 优先级 6，阻塞等待 q_gps_raw 指针，解析后更新 g_gps_data + 置 EVT_GPS_READY
 *
 * 只解析两条语句（其余一律忽略）：
 *   $GNRMC — 速度(speed) / 航向(heading) / 位置(lat,lon)
 *   $GNGGA — 定位质量(fix_quality) / 海拔(alt) / 卫星数(sats)
 *   → 位置只来自 RMC，GGA 不触碰 lat/lon，避免两条语句互相覆盖导致帧内容不确定
 */

#include "app_types.h"
#include "app_queues.h"
#include "main.h"
#include "cmsis_os2.h"
#include <string.h>
#include <stdlib.h>
#include <ctype.h>

gps_data_t g_gps_data;

/* ---- 调试计数器（定义在 main.c，Keil Watch 窗口查看） ---- */
extern volatile uint32_t dbg_gps_rx, dbg_gps_parse, dbg_gps_fix, dbg_gps_evt;

/* ---- NMEA 校验和 ---- */
 static int nmea_checksum_ok(const char *sentence)
{
    /* 定位 '*' 分隔符，校验和必须在 '*' 之后 */
    const char *star = strchr(sentence, '*');/*如果在字符串 str 中找到字符 c，则函数返回指向该字符的指针，如果未找到该字符则返回 NULL。*/
    if (!star) return 0;                    /* 无校验和 → 拒绝 */
    if (star[1] == '\0' || star[2] == '\0') return 0;  /* 截断 → 拒绝 */

    /* 计算校验和：'$' 之后、'*' 之前的所有字节 XOR */
    uint8_t calc = 0;
    /*  sentence  = 0x1000     // 指向 '$'
        star      = 0x101F     // 指向 '*'  ← 这是地址值，不是字符 '*/
    for (const char *p = sentence + 1; p < star; p++) {
        calc ^= (uint8_t)*p;
    }

    /* 解析期望校验和（2 字节十六进制） */
    char hex[3] = {star[1], star[2], '\0'};
    uint8_t expect = (uint8_t)strtol(hex, NULL, 16);/*将两个数组和变成16进制数*/

    return (calc == expect) ? 1 : 0;
}

/* ---- NMEA 解析辅助 ---- */
static double nmea_to_deg(const char *str, const char *hem)
{
    if (!str || !str[0] || !hem || !hem[0]) return 0.0;/*如果四个有一个为NULL则返回0.0*/
    double raw = atof(str);
    int deg = (int)(raw / 100.0);
    double min = raw - deg * 100.0;
    double val = deg + min / 60.0;
    return (*hem == 'S' || *hem == 'W') ? -val : val;
}

static float nmea_knots_to_ms(float knots)
{
    return knots * 0.514444f;
}

/* ---- 解析单句 NMEA ---- */
static int parse_nmea_sentence(const char *sentence, gps_data_t *out)
{
    /* ① 校验和验证（防噪声/断帧） */
    if (!nmea_checksum_ok(sentence)) return 0;

    /* 字段缓冲：static 移出栈！task_gps_rx 栈仅 512B，原 tmp[12][32]=384B 会撑爆栈 → HardFault 看门狗复位 */
    static char tmp[12][24];
    memset(tmp, 0, sizeof(tmp));

    /* ② 只处理 GGA 和 RMC（匹配任意 talker ID：$GPGGA/$GNGGA/$GNMGGA/$BDGGA 等） */
    if (strstr(sentence, "GGA,") != NULL)
    {
        char *p = (char*)sentence + 7; /* 跳过 "$xxGGA," */
        for (int i = 0; i < 10 && *p; i++) {
            char *end = strchr(p, ',');
            uint32_t flen = end ? (uint32_t)(end - p) : (uint32_t)strlen(p);
            if (flen >= sizeof(tmp[i])) flen = sizeof(tmp[i]) - 1;   /* 防异常字段越界写穿 */
            memcpy(tmp[i], p, flen); tmp[i][flen] = '\0';
            p = end ? end + 1 : p + flen;
        }
        /* GGA 只取：定位质量 / 海拔 / 卫星数（位置由 RMC 提供，这里不读 lat/lon 避免覆盖） */
        out->fix_quality = (uint8_t)atoi(tmp[5]);
        out->sats = (uint8_t)atoi(tmp[6]);
        out->alt  = atof(tmp[8]);
        return 1;
    }

    if (strstr(sentence, "RMC,") != NULL)
    {
        char *p = (char*)sentence + 7;
        for (int i = 0; i < 12 && *p; i++) {
            char *end = strchr(p, ',');
            uint32_t flen = end ? (uint32_t)(end - p) : (uint32_t)strlen(p);
            if (flen >= sizeof(tmp[i])) flen = sizeof(tmp[i]) - 1;   /* 防异常字段越界写穿 */
            memcpy(tmp[i], p, flen); tmp[i][flen] = '\0';
            p = end ? end + 1 : p + flen;
        }
        /* RMC status: A=有效, V=无效, 空=无数据 */
        if (tmp[1][0] != 'A') return 0;

        out->lat  = nmea_to_deg(tmp[2], tmp[3]);
        out->lon  = nmea_to_deg(tmp[4], tmp[5]);
        out->speed    = nmea_knots_to_ms(atof(tmp[6]));
        out->heading  = atof(tmp[7]);
        return 2;
    }

    return 0;
}

/* ================================================================
 * 任务入口
 * ================================================================ */
void StartTask_gps_rx(void *argument)
{
    gps_pdu_t *pdu;
    static gps_data_t data = {0};   /* 跨 PDU 累积字段：RMC 写 lat/lon/speed/heading，GGA 写 fix/sats/alt。
                                      否则 RMC 与 GGA 分两个 PDU 时，单条帧缺字段（如 0x123 全 0） */
    int gps_updated = 0;            /* 本周期是否解析到 GPS 句子 */

    for (;;) {
        if (osMessageQueueGet(q_gps_rawHandle, &pdu, NULL, osWaitForever) != osOK)
            continue;

        dbg_gps_rx++;   /* UART2 收到了数据 */

        /* 在 DMA 缓冲中逐句解析 NMEA */
        data.tick = HAL_GetTick();
        char *line_start = (char*)pdu->buf;
        char *line_end = line_start;
        /*struct结构体中地址排布从上到下，所以pdu->buf是结构体的第一个成员，即缓冲区的起始地址 pdu->len是缓冲区的长度*/
        while (line_end < (char*)pdu->buf + pdu->len) {
            line_end = memchr(line_start, '$', pdu->len - (line_start - (char*)pdu->buf));
            if (!line_end) break;

            char *eol = memchr(line_end, '\n', pdu->len - (line_end - (char*)pdu->buf));
            if (!eol) break;

            *eol = '\0';    
            if (parse_nmea_sentence(line_end, &data)) {
                dbg_gps_parse++;   /* NMEA 校验+格式通过 */
                gps_updated = 1;
            }
            *eol = '\n';

            line_start = eol + 1;
        }

        /* GGA/RMC 解析成功即发帧（室内无星时 lat/lon 为 0，由 U34 判 valid） */
        if (gps_updated) {
            dbg_gps_fix++;
            /* 写 g_gps_data 前拿锁，防止 task_nav/src 读到半更新数据 */
            if (osMutexAcquire(myMutex_nav_resultHandle, osWaitForever) == osOK) {
                g_gps_data = data;
                osMutexRelease(myMutex_nav_resultHandle);
            }
            osEventFlagsSet(eg_sensorHandle, EVT_GPS_READY);
            dbg_gps_evt++;
            gps_updated = 0;
        }
    }
}
