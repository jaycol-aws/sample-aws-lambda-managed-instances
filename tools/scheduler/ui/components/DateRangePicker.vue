// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div class="flex flex-wrap items-end gap-3">
    <button
      type="button"
      :class="[
        'px-3 py-1.5 text-sm font-medium rounded border',
        isPreset(7)
          ? 'bg-blue-600 text-white border-blue-600'
          : 'bg-slate-700 text-slate-300 border-slate-600 hover:bg-slate-600',
      ]"
      @click="applyPreset(7)"
    >
      Last 7 days
    </button>
    <button
      type="button"
      :class="[
        'px-3 py-1.5 text-sm font-medium rounded border',
        isPreset(30)
          ? 'bg-blue-600 text-white border-blue-600'
          : 'bg-slate-700 text-slate-300 border-slate-600 hover:bg-slate-600',
      ]"
      @click="applyPreset(30)"
    >
      Last 30 days
    </button>
    <div class="flex items-end gap-2">
      <label class="text-sm text-slate-400">
        From
        <input
          type="date"
          :value="start"
          class="block mt-1 px-2 py-1.5 text-sm bg-slate-700 border border-slate-600 text-slate-100 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
          @change="onCustomStart"
        >
      </label>
      <label class="text-sm text-slate-400">
        To
        <input
          type="date"
          :value="end"
          class="block mt-1 px-2 py-1.5 text-sm bg-slate-700 border border-slate-600 text-slate-100 rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
          @change="onCustomEnd"
        >
      </label>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  start: string
  end: string
}>()

const emit = defineEmits<{
  update: [range: { start: string; end: string }]
}>()

function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return formatDate(d)
}

function isPreset(days: number): boolean {
  return props.start === daysAgo(days) && props.end === formatDate(new Date())
}

function applyPreset(days: number) {
  emit('update', { start: daysAgo(days), end: formatDate(new Date()) })
}

function onCustomStart(e: Event) {
  const val = (e.target as HTMLInputElement).value
  if (val) emit('update', { start: val, end: props.end })
}

function onCustomEnd(e: Event) {
  const val = (e.target as HTMLInputElement).value
  if (val) emit('update', { start: props.start, end: val })
}
</script>
