/** 系统管理员用户管理 API。 */
import request from '@/utils/request'

export function getUserList(params) {
  return request.get('/user/list', { params })
}

export function getRoleList() {
  return request.get('/user/roles')
}

export function updateUserRoles(userId, roleNames) {
  return request.put(`/user/${userId}/roles`, { role_names: roleNames })
}

export function updateUserStatus(userId, isActive) {
  return request.put(`/user/${userId}/status`, { is_active: isActive })
}
