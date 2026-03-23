// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div class="bg-slate-800 border border-slate-700 rounded-lg p-4">
    <ClientOnly>
      <Bar v-if="chartType === 'bar'" :data="chartDataset" :options="chartOptions" />
      <Line v-else :data="chartDataset" :options="chartOptions" />
    </ClientOnly>
  </div>
</template>

<script setup lang="ts">
import { Line, Bar } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title, Tooltip, Legend, Filler)

const props = withDefaults(defineProps<{
  labels: string[]
  data: number[]
  title?: string
  chartType?: 'line' | 'bar'
}>(), {
  title: 'Savings Over Time',
  chartType: 'line',
})

const chartDataset = computed(() => ({
  labels: props.labels,
  datasets: [
    {
      label: 'Savings (USD)',
      data: props.data,
      borderColor: '#2563eb',
      backgroundColor: props.chartType === 'bar' ? 'rgba(37, 99, 235, 0.7)' : 'rgba(37, 99, 235, 0.1)',
      fill: props.chartType !== 'bar',
      tension: 0.3,
      pointRadius: 3,
    },
  ],
}))

const chartOptions = computed(() => ({
  responsive: true,
  plugins: {
    title: {
      display: true,
      text: props.title,
      color: '#f1f5f9',
    },
    legend: {
      display: false,
    },
  },
  scales: {
    x: {
      ticks: { color: '#94a3b8' },
      grid: { color: 'rgba(148, 163, 184, 0.1)' },
    },
    y: {
      beginAtZero: true,
      ticks: {
        color: '#94a3b8',
        callback: (value: number | string) => `$${value}`,
      },
      grid: { color: 'rgba(148, 163, 184, 0.1)' },
    },
  },
}))
</script>
