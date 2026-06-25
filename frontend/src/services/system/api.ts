// @ts-ignore
/* eslint-disable */
// 系统管理(RBAC)API:用户/角色/部门/菜单/权限
import { request } from '@umijs/max';

// ---- 用户 ----
export async function listUsers(
  params: { current?: number; pageSize?: number; keyword?: string; deptId?: string } = {},
  options?: { [key: string]: any },
) {
  return request<System.Page<System.User>>('/api/v1/system/users', {
    method: 'GET',
    params,
    ...(options || {}),
  });
}

export async function createUser(body: System.UserCreate, options?: { [key: string]: any }) {
  return request<{ data: System.User; success: boolean }>('/api/v1/system/users', {
    method: 'POST',
    data: body,
    ...(options || {}),
  });
}

export async function updateUser(id: string, body: System.UserUpdate, options?: { [key: string]: any }) {
  return request<{ data: System.User; success: boolean }>(`/api/v1/system/users/${id}`, {
    method: 'PUT',
    data: body,
    ...(options || {}),
  });
}

export async function resetUserPassword(id: string, password: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/system/users/${id}/password`, {
    method: 'PUT',
    data: { password },
    ...(options || {}),
  });
}

export async function deleteUser(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/system/users/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

// ---- 角色 ----
export async function listRoles(
  params: { current?: number; pageSize?: number; keyword?: string } = {},
  options?: { [key: string]: any },
) {
  return request<System.Page<System.Role>>('/api/v1/system/roles', {
    method: 'GET',
    params,
    ...(options || {}),
  });
}

export async function getRole(id: string, options?: { [key: string]: any }) {
  return request<{ data: System.RoleDetail; success: boolean }>(`/api/v1/system/roles/${id}`, {
    method: 'GET',
    ...(options || {}),
  });
}

export async function createRole(body: System.RoleCreate, options?: { [key: string]: any }) {
  return request<{ data: System.Role; success: boolean }>('/api/v1/system/roles', {
    method: 'POST',
    data: body,
    ...(options || {}),
  });
}

export async function updateRole(id: string, body: System.RoleUpdate, options?: { [key: string]: any }) {
  return request<{ data: System.Role; success: boolean }>(`/api/v1/system/roles/${id}`, {
    method: 'PUT',
    data: body,
    ...(options || {}),
  });
}

export async function deleteRole(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/system/roles/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

// ---- 部门 ----
export async function listDepts(options?: { [key: string]: any }) {
  return request<{ data: System.Department[]; success: boolean }>('/api/v1/system/depts', {
    method: 'GET',
    ...(options || {}),
  });
}

export async function createDept(body: System.DeptCreate, options?: { [key: string]: any }) {
  return request<{ data: System.Department; success: boolean }>('/api/v1/system/depts', {
    method: 'POST',
    data: body,
    ...(options || {}),
  });
}

export async function updateDept(id: string, body: System.DeptUpdate, options?: { [key: string]: any }) {
  return request<{ data: System.Department; success: boolean }>(`/api/v1/system/depts/${id}`, {
    method: 'PUT',
    data: body,
    ...(options || {}),
  });
}

export async function deleteDept(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/system/depts/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

// ---- 菜单 ----
export async function listMenus(options?: { [key: string]: any }) {
  return request<{ data: System.Menu[]; success: boolean }>('/api/v1/system/menus', {
    method: 'GET',
    ...(options || {}),
  });
}

export async function createMenu(body: System.MenuCreate, options?: { [key: string]: any }) {
  return request<{ data: System.Menu; success: boolean }>('/api/v1/system/menus', {
    method: 'POST',
    data: body,
    ...(options || {}),
  });
}

export async function updateMenu(id: string, body: System.MenuUpdate, options?: { [key: string]: any }) {
  return request<{ data: System.Menu; success: boolean }>(`/api/v1/system/menus/${id}`, {
    method: 'PUT',
    data: body,
    ...(options || {}),
  });
}

export async function deleteMenu(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/system/menus/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

// ---- 权限总览 ----
export async function permissionOverview(options?: { [key: string]: any }) {
  return request<{ data: System.PermOverview; success: boolean }>(
    '/api/v1/system/permissions/overview',
    { method: 'GET', ...(options || {}) },
  );
}
