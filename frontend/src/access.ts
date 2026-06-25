/**
 * @see https://umijs.org/docs/max/access#access
 *
 * RBAC:
 * - canAdmin:遗留(currentUser.access==='admin'),兼容旧写端点门控;
 * - canSystem:可见「系统管理」菜单(admin 或持任意 system:* 权限);
 * - hasPerm(code):按钮级门控,持通配 *:*:* 或精确码放行。
 * */
export default function access(
  initialState: { currentUser?: API.CurrentUser } | undefined,
) {
  const { currentUser } = initialState ?? {};
  const perms = new Set(currentUser?.permissions ?? []);
  const hasPerm = (code: string): boolean =>
    perms.has('*:*:*') || perms.has(code);
  const canAdmin = currentUser?.access === 'admin';
  const canSystem =
    canAdmin ||
    [...perms].some((p) => p === '*:*:*' || p.startsWith('system:'));
  return { canAdmin, canSystem, hasPerm };
}
