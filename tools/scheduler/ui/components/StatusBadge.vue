// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <span
    :class="[
      'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium',
      state === 'active'
        ? 'bg-green-900/30 text-green-400'
        : 'bg-amber-900/30 text-amber-400',
    ]"
  >
    {{ state === 'active' ? 'Active' : 'Scaled Down' }}
  </span>
</template>

<script setup lang="ts">
defineProps<{
  state: 'active' | 'scaled-down'
}>()
</script>
