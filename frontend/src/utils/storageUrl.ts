/** compose 内部 MinIO 域名(全容器化部署时 backend 以此签发 presigned URL,浏览器不可达)。 */
const INTERNAL_MINIO_ORIGIN = 'http://minio:9000';

/**
 * presigned URL 归一化为浏览器可达地址。
 *
 * - 内部域名形态(STORAGE_MINIO_ENDPOINT=http://minio:9000,如本地/一体化部署):
 *   改写为同源 /minio/ 路径,由 nginx 反代回 minio:9000。反代固定 Host 头为
 *   minio:9000,与签名时一致,SigV4(SignedHeaders=host)仍然有效。
 * - 其余形态(如生产直连宿主 IP http://10.60.1.60:9000):浏览器本就可达,原样返回。
 *
 * 仅用于浏览器直接消费的场景(<img> 内嵌、下载链接)。传给 kkFileView 的 URL
 * 不要改写——kk 在 compose 网络内服务端取件,内部域名对它才是可达的。
 */
export const toBrowserFileUrl = (url: string): string =>
  url.startsWith(`${INTERNAL_MINIO_ORIGIN}/`)
    ? url.replace(INTERNAL_MINIO_ORIGIN, '/minio')
    : url;
