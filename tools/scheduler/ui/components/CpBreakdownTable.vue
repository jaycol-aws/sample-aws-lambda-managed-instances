// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div class="overflow-x-auto">
    <table class="min-w-full text-sm">
      <thead>
        <tr class="border-b border-slate-700 text-left text-slate-400">
          <th class="py-2 pr-4 font-medium">CP Name</th>
          <th class="py-2 pr-4 font-medium text-right">Estimated Savings</th>
          <th class="py-2 pr-4 font-medium text-right">Hours Down</th>
          <th class="py-2 font-medium text-right">Events</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(cp, i) in sorted"
          :key="cp.capacity_provider"
          :class="i % 2 === 0 ? 'bg-slate-800' : 'bg-slate-900/40'"
        >
          <td class="py-2 pr-4">
            <NuxtLink
              :to="`/reports/${encodeURIComponent(cp.capacity_provider)}`"
              class="text-blue-400 hover:text-blue-300 hover:underline"
            >
              {{ cp.capacity_provider }}
            </NuxtLink>
          </td>
          <td class="py-2 pr-4 text-right font-medium text-green-400">
            ${{ cp.estimated_savings_usd.toFixed(2) }}
          </td>
          <td class="py-2 pr-4 text-right">
            {{ cp.hours_down.toFixed(1) }}
          </td>
          <td class="py-2 text-right">
            {{ cp.event_count }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import type { CpSummary } from '~/types'

const props = defineProps<{
  providers: CpSummary[]
}>()

const sorted = computed(() =>
  [...props.providers].sort((a, b) => b.estimated_savings_usd - a.estimated_savings_usd),
)
</script>
