import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);
dayjs.extend(timezone);

/** 全平台统一展示时区（北京）。后端时间戳为带 Z 的 UTC，前端按此转换。 */
const APP_TZ = 'Asia/Shanghai';

/**
 * 把后端 UTC 时间戳按北京时间展示为 `YYYY-MM-DD HH:mm:ss`。
 * 强制 Asia/Shanghai（不随浏览器时区漂移）；空值返回 '-'。
 */
export const formatDateTime = (
  value?: string | number | Date | null,
): string =>
  value ? dayjs(value).tz(APP_TZ).format('YYYY-MM-DD HH:mm:ss') : '-';

const numberFormatter = new Intl.NumberFormat('en-US');

/**
 * Format a number with thousand separators.
 * Replaces numeral(val).format('0,0')
 */
export const formatNumber = (val: number | string): string => {
  const parsed = Number(val);
  return Number.isFinite(parsed) ? numberFormatter.format(parsed) : '';
};

/**
 * Format a number as yuan currency string.
 * Replaces `¥ ${numeral(val).format('0,0')}`
 */
export const formatYuan = (val: number | string) => `¥ ${formatNumber(val)}`;
