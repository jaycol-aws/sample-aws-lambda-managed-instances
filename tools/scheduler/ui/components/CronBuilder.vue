// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

<template>
  <div class="space-y-3">
    <!-- Mode toggle -->
    <div class="flex bg-slate-900/50 rounded-md p-0.5 w-fit">
      <button
        type="button"
        :class="['px-3 py-1 text-xs font-medium rounded transition-colors duration-150 cursor-pointer',
          mode === 'builder' ? 'bg-slate-600 text-slate-100' : 'text-slate-400 hover:text-slate-300']"
        @click="mode = 'builder'"
      >
        Builder
      </button>
      <button
        type="button"
        :class="['px-3 py-1 text-xs font-medium rounded transition-colors duration-150 cursor-pointer',
          mode === 'expression' ? 'bg-slate-600 text-slate-100' : 'text-slate-400 hover:text-slate-300']"
        @click="switchToExpression"
      >
        Expression
      </button>
    </div>

    <!-- Builder mode -->
    <template v-if="mode === 'builder'">
      <!-- Time -->
      <div class="flex items-center gap-2">
        <select
          v-model="hour"
          class="bg-slate-700 border border-slate-600 text-slate-100 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
        >
          <option v-for="h in 24" :key="h - 1" :value="h - 1">{{ formatHour(h - 1) }}</option>
        </select>
        <span class="text-slate-500 text-sm font-medium">:</span>
        <select
          v-model="minute"
          class="bg-slate-700 border border-slate-600 text-slate-100 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
        >
          <option v-for="m in MINUTES" :key="m" :value="m">{{ m.toString().padStart(2, '0') }}</option>
        </select>
      </div>

      <!-- Days of week -->
      <div>
        <p class="text-xs text-slate-400 mb-1.5">Days</p>
        <div class="flex gap-1.5">
          <button
            v-for="day in DAYS"
            :key="day.value"
            type="button"
            :class="['w-9 h-9 rounded-md text-xs font-medium transition-colors duration-150 cursor-pointer',
              selectedDays.includes(day.value)
                ? 'bg-blue-600 text-white'
                : 'bg-slate-700 text-slate-400 hover:bg-slate-600 hover:text-slate-200']"
            @click="toggleDay(day.value)"
          >
            {{ day.label }}
          </button>
        </div>
        <div class="flex gap-3 mt-2">
          <button type="button" class="text-xs text-blue-400 hover:text-blue-300 cursor-pointer transition-colors duration-150" @click="selectWeekdays">Weekdays</button>
          <button type="button" class="text-xs text-blue-400 hover:text-blue-300 cursor-pointer transition-colors duration-150" @click="selectWeekend">Weekend</button>
          <button type="button" class="text-xs text-blue-400 hover:text-blue-300 cursor-pointer transition-colors duration-150" @click="selectAll">Every day</button>
        </div>
      </div>

      <!-- Preview -->
      <div v-if="selectedDays.length > 0" class="bg-slate-900/60 rounded-md p-3 border border-slate-700/50">
        <p class="text-xs text-slate-500 mb-1">Preview</p>
        <code class="text-sm text-blue-300">{{ cronExpression }}</code>
        <p class="text-xs text-slate-400 mt-1">{{ humanReadable }}</p>
      </div>
      <p v-else class="text-xs text-amber-400">Select at least one day.</p>
    </template>

    <!-- Expression mode -->
    <template v-else>
      <input
        v-model="rawExpression"
        type="text"
        placeholder="cron(0 22 ? * MON-FRI *)"
        class="w-full bg-slate-700 border border-slate-600 text-slate-100 placeholder:text-slate-500 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      <p class="text-xs text-slate-500">
        EventBridge format: <code class="text-blue-300/70">cron(minutes hours day-of-month month day-of-week year)</code>
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
const DAYS = [
  { label: 'Sun', value: 'SUN' },
  { label: 'Mon', value: 'MON' },
  { label: 'Tue', value: 'TUE' },
  { label: 'Wed', value: 'WED' },
  { label: 'Thu', value: 'THU' },
  { label: 'Fri', value: 'FRI' },
  { label: 'Sat', value: 'SAT' },
]

const MINUTES = [0, 15, 30, 45]

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const mode = ref<'builder' | 'expression'>('builder')
const hour = ref(18)
const minute = ref(0)
const selectedDays = ref<string[]>(['MON', 'TUE', 'WED', 'THU', 'FRI'])
const rawExpression = ref('')

function initFromValue(val: string) {
  if (!val) return
  const parsed = parseCron(val)
  if (parsed) {
    hour.value = parsed.hour
    minute.value = MINUTES.includes(parsed.minute) ? parsed.minute : 0
    selectedDays.value = parsed.days
    mode.value = 'builder'
  } else {
    mode.value = 'expression'
    rawExpression.value = val
  }
}

onMounted(() => {
  if (props.modelValue) {
    initFromValue(props.modelValue)
  } else {
    // No initial value — emit the default so the parent form is populated immediately
    emit('update:modelValue', cronExpression.value)
  }
})

watch(() => props.modelValue, (val) => {
  if (val && val !== cronExpression.value) initFromValue(val)
})

const cronExpression = computed(() => {
  if (selectedDays.value.length === 0) return ''
  const dow = selectedDays.value.length === 7 ? '*' : buildDowExpression(selectedDays.value)
  return `cron(${minute.value} ${hour.value} ? * ${dow} *)`
})

const humanReadable = computed(() => {
  if (selectedDays.value.length === 0) return ''
  const timeStr = formatTime(hour.value, minute.value)
  if (selectedDays.value.length === 7) return `Every day at ${timeStr}`
  const sorted = sortDays(selectedDays.value)
  const weekdays = ['MON', 'TUE', 'WED', 'THU', 'FRI']
  const weekend = ['SAT', 'SUN']
  if (sorted.length === 5 && weekdays.every(d => sorted.includes(d))) return `Every weekday at ${timeStr}`
  if (sorted.length === 2 && weekend.every(d => sorted.includes(d))) return `Every weekend at ${timeStr}`
  return `Every ${sorted.map(d => DAYS.find(day => day.value === d)!.label).join(', ')} at ${timeStr}`
})

watch([hour, minute, selectedDays], () => {
  if (mode.value === 'builder' && cronExpression.value) {
    emit('update:modelValue', cronExpression.value)
  }
}, { deep: true })

watch(rawExpression, (val) => {
  if (mode.value === 'expression') emit('update:modelValue', val)
})

function toggleDay(day: string) {
  const idx = selectedDays.value.indexOf(day)
  if (idx === -1) {
    selectedDays.value.push(day)
  } else if (selectedDays.value.length > 1) {
    selectedDays.value.splice(idx, 1)
  }
}

function selectWeekdays() { selectedDays.value = ['MON', 'TUE', 'WED', 'THU', 'FRI'] }
function selectWeekend() { selectedDays.value = ['SAT', 'SUN'] }
function selectAll() { selectedDays.value = DAYS.map(d => d.value) }

function switchToExpression() {
  rawExpression.value = cronExpression.value || props.modelValue
  mode.value = 'expression'
}

function formatHour(h: number): string {
  const period = h >= 12 ? 'PM' : 'AM'
  const display = h % 12 || 12
  return `${display}:00 ${period}`
}

function formatTime(h: number, m: number): string {
  const period = h >= 12 ? 'PM' : 'AM'
  const display = h % 12 || 12
  return `${display}:${m.toString().padStart(2, '0')} ${period}`
}

function sortDays(days: string[]): string[] {
  return [...days].sort((a, b) => DAYS.findIndex(d => d.value === a) - DAYS.findIndex(d => d.value === b))
}

function buildDowExpression(days: string[]): string {
  const sorted = sortDays(days)
  const indices = sorted.map(d => DAYS.findIndex(day => day.value === d))
  let contiguous = true
  for (let i = 1; i < indices.length; i++) {
    if (indices[i] !== indices[i - 1] + 1) { contiguous = false; break }
  }
  return contiguous && sorted.length >= 3
    ? `${sorted[0]}-${sorted[sorted.length - 1]}`
    : sorted.join(',')
}

function parseCron(expr: string): { hour: number; minute: number; days: string[] } | null {
  const match = expr.match(/^cron\((.+)\)$/)
  if (!match) return null
  const parts = match[1].trim().split(/\s+/)
  if (parts.length < 5) return null
  const [min, hr, , , dow] = parts
  const h = parseInt(hr)
  const m = parseInt(min)
  if (isNaN(h) || isNaN(m)) return null
  return { hour: h, minute: m, days: parseDow(dow) }
}

function parseDow(dow: string): string[] {
  if (!dow || dow === '*' || dow === '?') return DAYS.map(d => d.value)
  const range = dow.match(/^(\w+)-(\w+)$/)
  if (range) {
    const start = DAYS.findIndex(d => d.value === range[1])
    const end = DAYS.findIndex(d => d.value === range[2])
    if (start !== -1 && end !== -1) return DAYS.slice(start, end + 1).map(d => d.value)
  }
  return dow.split(',').filter(d => DAYS.some(day => day.value === d))
}
</script>
