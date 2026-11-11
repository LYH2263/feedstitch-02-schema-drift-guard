<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const router = useRouter()
const runs = ref([])
const runId = ref(route.query.run_id ? Number(route.query.run_id) : null)
const rows = ref([])
const stats = ref(null)
const err = ref('')

const DISPOSITION_LABEL = {
  insert: '新增写入',
  upsert: '更新写入',
  quarantine: '冲突隔离',
  skipped_watermark: '水位线跳过',
  skip_stale: '旧版跳过',
  schema_drift: '结构漂移',
}

const META_KEYS = new Set(['staging_total', 'total_accounted', 'schema_drift'])
const dispositionEntries = computed(() => {
  if (!stats.value) return []
  return Object.entries(stats.value).filter(([key]) => !META_KEYS.has(key))
})

async function load() {
  err.value = ''
  try {
    const qs = runId.value ? `?run_id=${runId.value}` : ''
    rows.value = await getJSON(`/api/staging${qs}`)
    stats.value = await getJSON(`/api/staging/stats${qs}`)
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function onSelect() {
  router.replace({ query: runId.value ? { run_id: runId.value } : {} })
  load()
}

onMounted(async () => {
  runs.value = await getJSON('/api/runs')
  await load()
})
watch(runId, onSelect)
</script>
<template>
  <div class="page">
    <h1>Staging</h1>
    <p class="muted">
      每条解析记录的处置结果。结构漂移行进独立的「结构漂移」列表，这里以 schema_drift
      虚拟处置一并计入统计，保证 parsed = staging 行 + 漂移行。
    </p>
    <div class="panel">
      <label>
        按运行查看
        <select v-model.number="runId">
          <option :value="null">全部运行</option>
          <option v-for="r in runs" :key="r.id" :value="r.id">
            #{{ r.id }} · {{ r.source_name }} · {{ r.mode }}
          </option>
        </select>
      </label>
      <router-link v-if="runId" style="margin-left:1rem" :to="`/drifts?run_id=${runId}`">
        查看本次漂移行 →
      </router-link>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel" v-if="stats">
      <h3>处置统计（与运行报告同源）</h3>
      <p>
        staging 行 <strong>{{ stats.staging_total }}</strong>
        + 结构漂移 <strong>{{ stats.schema_drift }}</strong>
        = 已处置 <strong>{{ stats.total_accounted }}</strong>
      </p>
      <p>
        <span v-for="[key, count] in dispositionEntries" :key="key"
              class="tag" style="margin-right:0.5rem"
              :class="key === 'quarantine' ? 'danger' : (key.startsWith('skip') ? 'warn' : 'ok')">
          {{ DISPOSITION_LABEL[key] || key }} {{ count }}
        </span>
        <span class="tag danger" style="margin-right:0.5rem">
          {{ DISPOSITION_LABEL.schema_drift }} {{ stats.schema_drift }}
        </span>
      </p>
    </div>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>run</th><th>source</th><th>key</th><th>event_ts</th>
            <th>rev</th><th>disposition</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.run_id }}</td>
            <td>{{ r.source_id }}</td>
            <td class="mono">{{ r.business_key }}</td>
            <td class="mono">{{ r.event_ts }}</td>
            <td>{{ r.source_revision }}</td>
            <td>
              <span
                class="tag"
                :class="r.disposition === 'quarantine' ? 'danger' : (r.disposition.startsWith('skip') ? 'warn' : 'ok')"
              >{{ DISPOSITION_LABEL[r.disposition] || r.disposition }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="muted">当前视图没有 staging 行。</p>
    </div>
  </div>
</template>
