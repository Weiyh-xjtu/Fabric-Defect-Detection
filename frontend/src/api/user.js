/** 系统管理员用户管理 API。 */
import request from '@/utils/request'

export function getUserList(params) {
  return request.get('/user/list', { params })
}

export function getRoleList() {
  return request.get('/user/roles')
}

export function getPermissionList() {
  return request.get('/user/permissions')
}

export function updateRolePermissions(roleId, permissionCodes) {
  return request.put(`/user/roles/${roleId}/permissions`, {
    permission_codes: permissionCodes,
  })
}

export function createRole(payload) {
  return request.post('/user/roles', payload)
}

export function updateRole(roleId, payload) {
  return request.put(`/user/roles/${roleId}`, payload)
}

export function deleteRole(roleId) {
  return request.delete(`/user/roles/${roleId}`)
}

export function updateUserRoles(userId, roleNames) {
  return request.put(`/user/${userId}/roles`, { role_names: roleNames })
}

export function updateUserStatus(userId, isActive) {
  return request.put(`/user/${userId}/status`, { is_active: isActive })
}

export function uploadUserAvatar(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request.put('/user/avatar', formData)
}

export function removeUserAvatar() {
  return request.delete('/user/avatar')
}
