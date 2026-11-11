<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const router = useRouter()
const runs = ref([])
const runId = ref(route.query.run_id ? Number(route.query.run_id) : null)
const rows = ref([])
const err = ref('')

const KIND_LABEL = {
  missing_required: '必填缺失',
  type_mismatch: '类型不符',
  undeclared_field: '未声明字段',
}

async function load() {
  err.value = ''
  try {
    const path = runId.value ? `/api/schema-drifts?run_id=${runId.value}` : '/api/schema-drifts'
    rows.value = await getJSON(path)
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
    <h1>结构漂移列表</h1>
    <p class="muted">
      被源级字段契约拦下的记录：必填缺失 / 类型不符 / 未声明字段。这些行不会进入 Staging 归并，
      也不会写入 Canonical；条数与运行报告的 drift_count 同源。
    </p>
    <div class="panel">
      <label>
        按运行查看
        <select v-model.number="runId">
          <option :value="null">全部运行</option>
          <option v-for="r in runs" :key="r.id" :value="r.id">
            #{{ r.id }} · {{ r.source_name }} · {{ r.mode }} · drift {{ r.drift_count }}
          </option>
        </select>
      </label>
      <span style="margin-left:1rem">
        当前视图 <strong>{{ rows.length }}</strong> 条漂移
      </span>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel" v-for="d in rows" :key="d.id">
      <p>
        #{{ d.id }} · run {{ d.run_id }} · source {{ d.source_id }} ·
        <span class="mono">{{ d.business_key || '(无业务键)' }}</span> ·
        <span v-for="k in d.drift_kinds" :key="k" class="tag danger" style="margin-left:0.35rem">
          {{ KIND_LABEL[k] || k }}
        </span>
        <span class="muted"> · {{ d.created_at }}</span>
      </p>
      <table>
        <thead>
          <tr><th>问题字段</th><th>类型</th><th>期望</th><th>实际值</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="(f, i) in d.findings" :key="i">
            <td class="mono">{{ f.field }}</td>
            <td><span class="tag warn">{{ KIND_LABEL[f.kind] || f.kind }}</span></td>
            <td class="mono">{{ f.expected }}</td>
            <td class="mono">{{ f.actual === null ? '∅（缺失/空）' : JSON.stringify(f.actual) }}</td>
            <td class="muted">{{ f.detail }}</td>
          </tr>
        </tbody>
      </table>
      <div style="margin-top:0.6rem">
        <div class="muted">夹具原始记录（字段对比依据）</div>
        <pre class="mono">{{ JSON.stringify(d.raw_payload, null, 2) }}</pre>
      </div>
    </div>
    <p v-if="!rows.length" class="muted">当前视图没有结构漂移。</p>
  </div>
</template>
<style scoped>
pre {
  background: #0a1422;
  padding: 0.75rem;
  border-radius: 8px;
  overflow: auto;
  max-height: 260px;
}
</style>
