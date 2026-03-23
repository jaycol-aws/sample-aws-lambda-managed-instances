// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { AggregateReportSchema, CpReportSchema } from '~/schemas/report'
import type { AggregateReport, CpReport } from '~/types'

export interface ChartData {
  labels: string[]
  savings: number[]
  durations: number[]
}

function buildQuery(start?: string, end?: string): string {
  const params = new URLSearchParams()
  if (start) params.set('start', start)
  if (end) params.set('end', end)
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

function toChartData(report: CpReport): ChartData {
  return {
    labels: report.events.map(e => e.scale_down_time),
    savings: report.events.map(e => e.estimated_savings_usd),
    durations: report.events.map(e => e.duration_hours),
  }
}

export function useReports() {
  const api = useApi()

  const aggregateReport = ref<AggregateReport | null>(null)
  const cpReport = ref<CpReport | null>(null)
  const chartData = ref<ChartData | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchAggregate(start?: string, end?: string) {
    loading.value = true
    error.value = null
    try {
      aggregateReport.value = await api.get(
        `/reports${buildQuery(start, end)}`,
        AggregateReportSchema,
      )
    }
    catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
    finally {
      loading.value = false
    }
  }

  async function fetchCpReport(cpName: string, start?: string, end?: string) {
    loading.value = true
    error.value = null
    try {
      cpReport.value = await api.get(
        `/reports/${encodeURIComponent(cpName)}${buildQuery(start, end)}`,
        CpReportSchema,
      )
      chartData.value = toChartData(cpReport.value)
    }
    catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
    finally {
      loading.value = false
    }
  }

  return { aggregateReport, cpReport, chartData, loading, error, fetchAggregate, fetchCpReport }
}
