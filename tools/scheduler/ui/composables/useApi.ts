// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import type { ZodSchema } from 'zod'

export function useApi() {
  const config = useRuntimeConfig()
  const baseUrl = config.public.apiBaseUrl

  async function get<T>(path: string, schema: ZodSchema<T>): Promise<T> {
    const data = await $fetch(`${baseUrl}${path}`)
    return schema.parse(data)
  }

  async function post<T>(path: string, body?: Record<string, unknown>, schema?: ZodSchema<T>): Promise<T | void> {
    const data = await $fetch(`${baseUrl}${path}`, { method: 'POST', body })
    return schema ? schema.parse(data) : undefined
  }

  async function put<T>(path: string, body?: Record<string, unknown>, schema?: ZodSchema<T>): Promise<T | void> {
    const data = await $fetch(`${baseUrl}${path}`, { method: 'PUT', body })
    return schema ? schema.parse(data) : undefined
  }

  async function del<T>(path: string, schema?: ZodSchema<T>): Promise<T | void> {
    const data = await $fetch(`${baseUrl}${path}`, { method: 'DELETE' })
    return schema ? schema.parse(data) : undefined
  }

  return { get, post, put, del }
}
