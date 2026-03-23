// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div>
    <NuxtLink to="/reports" class="text-sm text-blue-400 hover:text-blue-300 hover:underline mb-4 inline-block">
      &larr; Back to Reports
    </NuxtLink>

    <h1 class="text-xl font-semibold text-slate-100 mb-4">{{ cpName }}</h1>

    <DateRangePicker
      :start="dateRange.start"
      :end="dateRange.end"
      class="mb-6"
      @update="onDateChange"
    />

    <!-- Loading -->
    <div v-if="loading" class="space-y-4">
      <div class="h-24 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
      <div class="h-64 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
      <div class="h-48 bg-slate-800 rounded-lg border border-slate-700 animate-pulse" />
    </div>

    <!-- Error -->
    <div v-else-if="error" class="bg-red-50 border border-red-200 rounded-lg p-8 text-center">
      <svg class="mx-auto mb-3 h-8 w-8 text-red-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
      </svg>
      <p class="text-sm font-semibold text-red-800 mb-1">Failed to load report</p>
      <p class="text-xs text-red-600 mb-4">{{ error }}</p>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-red-700 bg-white border border-red-300 rounded-md shadow-sm hover:bg-red-50 transition-colors duration-150 cursor-pointer"
        @click="refresh"
      >
        <svg class="h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
        </svg>
        Retry
      </button>
    </div>

    <!-- Empty state -->
    <div
      v-else-if="cpReport && cpReport.events.length === 0"
      class="bg-slate-800 border border-slate-700 rounded-lg p-10 text-center"
    >
      <p class="text-slate-400">No cost events found for {{ cpName }} in the selected date range.</p>
    </div>

    <!-- Report content -->
    <div v-else-if="cpReport" class="space-y-6">
      <!-- Summary stats -->
      <div class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-5">
        <div class="flex flex-wrap gap-8">
          <div>
            <p class="text-sm text-slate-400">Total Estimated Savings</p>
            <p class="text-2xl font-bold text-green-400">${{ cpReport.total_estimated_savings_usd.toFixed(2) }}</p>
          </div>
          <div>
            <p class="text-sm text-slate-400">Hours Down</p>
            <p class="text-2xl font-bold text-slate-100">{{ cpReport.total_hours_down.toFixed(1) }}</p>
          </div>
          <div>
            <p class="text-sm text-slate-400">Events</p>
            <p class="text-2xl font-bold text-slate-100">{{ cpReport.event_count }}</p>
          </div>
        </div>
      </div>

      <!-- Chart -->
      <SavingsChart
        v-if="chartData"
        :labels="chartData.labels"
        :data="chartData.savings"
        :title="`Savings — ${cpName}`"
      />

      <!-- Cost events table -->
      <div class="bg-slate-800 rounded-lg border border-slate-700 shadow-sm p-5">
        <h2 class="text-base font-semibold text-slate-100 mb-3">Cost Events</h2>
        <div class="overflow-x-auto">
          <table class="min-w-full text-sm">
            <thead>
              <tr class="border-b border-slate-700 text-left text-slate-400">
                <th class="py-2 pr-4 font-medium">Scale Down</th>
                <th class="py-2 pr-4 font-medium">Scale Up</th>
                <th class="py-2 pr-4 font-medium text-right">Duration (hrs)</th>
                <th class="py-2 font-medium text-right">Savings</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(ev, i) in cpReport.events"
                :key="i"
                :class="i % 2 === 0 ? 'bg-slate-800' : 'bg-slate-900/40'"
              >
                <td class="py-2 pr-4 text-slate-300">{{ ev.scale_down_time }}</td>
                <td class="py-2 pr-4 text-slate-300">{{ ev.scale_up_time ?? '—' }}</td>
                <td class="py-2 pr-4 text-right text-slate-300">{{ ev.duration_hours.toFixed(1) }}</td>
                <td class="py-2 text-right font-medium text-green-400">${{ ev.estimated_savings_usd.toFixed(2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const route = useRoute()
const cpName = decodeURIComponent(route.params.cpName as string)

const { cpReport, chartData, loading, error, fetchCpReport } = useReports()

function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return formatDate(d)
}

const dateRange = reactive({
  start: daysAgo(30),
  end: formatDate(new Date()),
})

function onDateChange(range: { start: string; end: string }) {
  dateRange.start = range.start
  dateRange.end = range.end
  refresh()
}

function refresh() {
  fetchCpReport(cpName, dateRange.start, dateRange.end)
}

onMounted(() => {
  refresh()
})
</script>
