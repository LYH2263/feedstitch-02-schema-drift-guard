<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const sources = ref([])
const sourceId = ref(null)
const mode = ref('incremental')
const last = ref(null)
const history = ref([])
const err = ref('')
const busy = ref(false)

async function refresh() {
  history.value = await getJSON('/api/runs')
}

async function runOnce() {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON(`/api/sources/${sourceId.value}/runs`, { mode: mode.value })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  sources.value = await getJSON('/api/sources')
  if (sources.value.length) sourceId.value = sources.value[0].id
  await refresh()
})
</script>
<template>
  <div class="page">
    <h1>运行台</h1>
    <p class="muted">
      选源触发增量或全量；每行先过源级字段契约（漂移拦截），再走水位线与业务键归并。
      报告数字与 Staging 处置统计、漂移列表、Canonical 列表同源可对账。
    </p>
    <div class="panel">
      <label>
        源
        <select v-model.number="sourceId">
          <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.id }}. {{ s.name }}</option>
        </select>
      </label>
      <label style="margin-left:1rem">
        模式
        <select v-model="mode">
          <option value="incremental">incremental</option>
          <option value="full">full</option>
        </select>
      </label>
      <button style="margin-left:1rem" :disabled="busy || !sourceId" @click="runOnce">
        {{ busy ? '运行中…' : '执行 Run' }}
      </button>
      <router-link style="margin-left:1rem" to="/contracts">配置该源契约 →</router-link>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel" v-if="last">
      <h3>最近一次报告 · Run #{{ last.id }}</h3>
      <p>
        <span class="tag" :class="last.status === 'ok' ? 'ok' : 'danger'">{{ last.status }}</span>
        · parsed <strong>{{ last.parsed_count }}</strong>
        · upserted <strong>{{ last.upserted_count }}</strong>
        · conflict <strong>{{ last.conflict_count }}</strong>
        · skipped <strong>{{ last.skipped_count }}</strong>
        · drift <strong :class="last.drift_count ? 'tag danger' : ''">{{ last.drift_count }}</strong>
      </p>
      <p class="mono muted">
        canonical_count={{ last.report?.canonical_count }} · watermark={{ last.report?.watermark_ts }}
      </p>
      <div v-if="last.report?.reconciliation" class="recon">
        <p>
          对账：
          <span class="tag" :class="last.report.reconciliation.balanced ? 'ok' : 'danger'">
            {{ last.report.reconciliation.balanced ? '数字闭环' : '对账不平' }}
          </span>
          parsed {{ last.report.reconciliation.parsed_count }}
          = staging {{ last.report.reconciliation.staging_total }}
          + 漂移 {{ last.report.reconciliation.drift_rows }}
          = {{ last.report.reconciliation.accounted_count }}
        </p>
        <p class="mono muted" style="font-size:0.85rem">
          staging 处置分布：{{ JSON.stringify(last.report.reconciliation.staging_dispositions) }}
        </p>
        <p>
          <router-link :to="`/staging?run_id=${last.id}`">查看本次 Staging 行 →</router-link>
          <router-link style="margin-left:1rem" :to="`/drifts?run_id=${last.id}`">
            查看本次漂移行 ({{ last.drift_count }}) →
          </router-link>
          <router-link style="margin-left:1rem" to="/canonical">Canonical 列表 →</router-link>
        </p>
      </div>
    </div>

    <div class="panel">
      <h3>历史 Runs</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>源</th><th>模式</th><th>状态</th>
            <th>parsed</th><th>upserted</th><th>conflict</th><th>skipped</th>
            <th>drift</th><th>时间</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in history" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.source_name }}</td>
            <td>{{ r.mode }}</td>
            <td><span class="tag" :class="r.status === 'ok' ? 'ok' : 'danger'">{{ r.status }}</span></td>
            <td>{{ r.parsed_count }}</td>
            <td>{{ r.upserted_count }}</td>
            <td>{{ r.conflict_count }}</td>
            <td>{{ r.skipped_count }}</td>
            <td>
              <span :class="r.drift_count ? 'tag danger' : 'muted'">{{ r.drift_count }}</span>
            </td>
            <td class="mono">{{ r.created_at }}</td>
            <td>
              <router-link :to="`/drifts?run_id=${r.id}`">漂移</router-link>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
