// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import tailwindcss from '@tailwindcss/vite'

// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  ssr: false,

  css: ['~/tailwind.css'],

  vite: {
    plugins: [tailwindcss() as any]
  },

  runtimeConfig: {
    public: {
      apiBaseUrl: '' // Set via NUXT_PUBLIC_API_BASE_URL env var at build time
    }
  },

  compatibilityDate: '2025-01-01'
})
