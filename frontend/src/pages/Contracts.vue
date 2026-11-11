<script setup>
import { onMounted, ref, watch } from 'vue'
import { getJSON, putJSON, postJSON } from '../api'

const sources = ref([])
const sourceId = ref(null)
const contract = ref(null)
const draft = ref({ allow_undeclared: false, fields: [] })
const preview = ref(null)
const err = ref('')
const msg = ref('')
const busy = ref(false)
const newFieldName = ref('')
const newFieldType = ref('string')
const TYPES = ['string', 'number', 'integer', 'boolean']
// Core fields back the NOT NULL / typed staging and canonical columns; the
// backend enforces they stay required with a fixed type, so lock them in UI.
const CORE_FIELDS = ['business_key', 'title', 'category', 'event_ts', 'price', 'source_revision']

function toDraft(contractPayload) {
  return {
    allow_undeclared: !!contractPayload.allow_undeclared,
    fields: Object.entries(contractPayload.fields || {}).map(([name, spec]) => ({
      name,
      required: !!spec.required,
      type: spec.type,
    })),
  }
}

async function loadContract() {
  if (!sourceId.value) return
  err.value = ''
  try {
    const row = await getJSON(`/api/sources/${sourceId.value}/contract`)
    contract.value = row
    draft.value = toDraft(row.contract)
    await loadPreview()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function loadPreview() {
  preview.value = null
  try {
    // Dry-run the parser against the *saved* contract; no writes.
    preview.value = await getJSON(`/api/sources/${sourceId.value}/preview`)
  } catch (e) {
    // preview is best-effort on this page
    preview.value = null
  }
}

function addField() {
  const name = newFieldName.value.trim()
  if (!name) return
  if (draft.value.fields.some((f) => f.name === name)) {
    err.value = `字段 ${name} 已存在`
    return
  }
  draft.value.fields.push({ name, required: false, type: newFieldType.value })
  newFieldName.value = ''
  err.value = ''
}

function removeField(name) {
  draft.value.fields = draft.value.fields.filter((f) => f.name !== name)
}

async function save() {
  err.value = ''
  msg.value = ''
  if (!draft.value.fields.length) {
    err.value = '契约至少要声明一个字段'
    return
  }
  busy.value = true
  try {
    const body = {
      allow_undeclared: draft.value.allow_undeclared,
      fields: Object.fromEntries(
        draft.value.fields.map((f) => [f.name, { required: f.required, type: f.type }]),
      ),
    }
    const saved = await putJSON(`/api/sources/${sourceId.value}/contract`, body)
    contract.value = saved
    draft.value = toDraft(saved.contract)
    msg.value = `契约已保存（${saved.updated_at}）`
    await loadPreview()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function reset() {
  err.value = ''
  msg.value = ''
  busy.value = true
  try {
    const saved = await postJSON(`/api/sources/${sourceId.value}/contract/reset`)
    contract.value = saved
    draft.value = toDraft(saved.contract)
    msg.value = '已恢复为默认核心字段契约'
    await loadPreview()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  sources.value = await getJSON('/api/sources')
  if (sources.value.length) sourceId.value = sources.value[0].id
})
watch(sourceId, loadContract)
</script>
<template>
  <div class="page">
    <h1>源级字段契约</h1>
    <p class="muted">
      每个源维护一份期望 Schema：字段是否必填、期望类型。运行时必填缺失 / 类型不符 / 未声明字段
      都会记为结构漂移，漂移行进漂移列表且不得写入 Canonical。
    </p>
    <div class="panel">
      <label>
        源
        <select v-model.number="sourceId">
          <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.id }}. {{ s.name }}</option>
        </select>
      </label>
      <span v-if="contract" class="muted mono" style="margin-left:1rem">
        最近保存：{{ contract.updated_at }}
      </span>
    </div>

    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="msg" class="tag ok">{{ msg }}</p>

    <div class="panel" v-if="preview">
      <h3>按当前已保存契约预演夹具（不落库）</h3>
      <p>
        夹具共 <strong>{{ preview.count }}</strong> 行，
        <span :class="preview.drift_count ? 'tag danger' : 'tag ok'">
          结构漂移 {{ preview.drift_count }} 行
        </span>
        <span class="muted"> · 保存契约后可在「运行台」执行 Run 验证拦截效果</span>
      </p>
    </div>

    <div class="panel" v-if="draft.fields.length">
      <h3>字段定义</h3>
      <p class="muted">
        六个核心字段对应 Staging / Canonical 的落库列，保持必填且类型锁定；可自由新增、删除自定义字段。
      </p>
      <table>
        <thead>
          <tr><th>字段名</th><th>必填</th><th>期望类型</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="f in draft.fields" :key="f.name">
            <td class="mono">
              {{ f.name }}
              <span v-if="CORE_FIELDS.includes(f.name)" class="tag ok" style="margin-left:0.4rem">核心</span>
            </td>
            <td>
              <input
                type="checkbox"
                v-model="f.required"
                :disabled="CORE_FIELDS.includes(f.name)"
              />
            </td>
            <td>
              <select v-model="f.type" :disabled="CORE_FIELDS.includes(f.name)">
                <option v-for="t in TYPES" :key="t" :value="t">{{ t }}</option>
              </select>
            </td>
            <td>
              <button
                v-if="!CORE_FIELDS.includes(f.name)"
                class="secondary"
                @click="removeField(f.name)"
              >删除</button>
              <span v-else class="muted">落库字段，锁定</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div style="margin-top:0.75rem; display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap">
        <input v-model="newFieldName" placeholder="新字段名" style="width:12rem" />
        <select v-model="newFieldType">
          <option v-for="t in TYPES" :key="t" :value="t">{{ t }}</option>
        </select>
        <button class="secondary" @click="addField">添加字段</button>
      </div>
    </div>

    <div class="panel">
      <h3>未声明字段策略</h3>
      <label>
        <input type="checkbox" v-model="draft.allow_undeclared" />
        允许夹具出现契约之外的字段（默认关闭；关闭时多余字段记为 undeclared_field 漂移）
      </label>
    </div>

    <div class="panel">
      <button :disabled="busy" @click="save">{{ busy ? '保存中…' : '保存契约' }}</button>
      <button class="secondary" style="margin-left:0.75rem" :disabled="busy" @click="reset">
        恢复默认契约
      </button>
    </div>
  </div>
</template>
