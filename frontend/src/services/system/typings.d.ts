// @ts-ignore
/* eslint-disable */
// 系统管理(RBAC)类型:用户/角色/部门/菜单/权限

declare namespace System {
  // ---- 角色 ----
  type Role = {
    id: string;
    name: string;
    roleKey: string;
    sort: number;
    dataScope: string; // all | custom | dept | dept_and_child | self
    status: string; // "0" 正常 / "1" 停用
    remark?: string;
    createdAt: string;
  };
  type RoleDetail = Role & { menuIds: string[]; deptIds: string[] };
  type RoleCreate = {
    name: string;
    roleKey: string;
    sort?: number;
    dataScope?: string;
    status?: string;
    remark?: string;
    menuIds?: string[];
    deptIds?: string[];
  };
  type RoleUpdate = Partial<RoleCreate>;

  // ---- 部门 ----
  type Department = {
    id: string;
    parentId?: string;
    ancestors: string;
    name: string;
    sort: number;
    leader?: string;
    phone?: string;
    email?: string;
    status: string;
    userCount?: number;
    children?: Department[];
  };
  type DeptCreate = {
    name: string;
    parentId?: string;
    sort?: number;
    leader?: string;
    phone?: string;
    email?: string;
    status?: string;
  };
  type DeptUpdate = Partial<DeptCreate>;

  // ---- 菜单 ----
  type Menu = {
    id: string;
    parentId?: string;
    name: string;
    menuType: string; // M | C | F
    path?: string;
    component?: string;
    perms?: string;
    icon?: string;
    sort: number;
    visible: string; // "0" 显示 / "1" 隐藏
    status: string;
    isFrame: boolean;
    children?: Menu[];
  };
  type MenuCreate = {
    name: string;
    parentId?: string;
    menuType: string;
    path?: string;
    component?: string;
    perms?: string;
    icon?: string;
    sort?: number;
    visible?: string;
    status?: string;
    isFrame?: boolean;
  };
  type MenuUpdate = Partial<MenuCreate>;

  // ---- 用户 ----
  type User = {
    id: string;
    username: string;
    displayName?: string;
    deptId?: string;
    disabled: boolean;
    roles: string[]; // role_key 列表
    createdAt: string;
  };
  type UserCreate = {
    username: string;
    password: string;
    displayName?: string;
    deptId?: string;
    roleIds: string[];
  };
  type UserUpdate = {
    displayName?: string;
    deptId?: string;
    roleIds?: string[];
    disabled?: boolean;
  };

  // ---- 通用 ----
  type Page<T> = { data: T[]; total: number; success: boolean };
  type PermOverview = {
    roles: { id: string; name: string; roleKey: string; perms: string[] }[];
    allPerms: string[];
  };
}
