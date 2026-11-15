<script setup>
import { ref } from 'vue'

const goal = ref('')
const sessionId = ref(null)
const cards = ref([])
const busy = ref(false)
const error = ref('')
const status = ref('填写学习目标，开始一次引导式练习。')
const statusLabels = {
  correct: '回答正确',
  partial: '部分正确',
  incorrect: '回答有误',
  needs_clarification: '需要澄清',
  invalid_exercise: '题目无效',
}

async function post(path, body) {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (response.status === 404) {
    sessionId.value = null
    throw new Error('会话或题目已失效，请点击“开始新练习”重新开始。')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string' ? data.detail : `请求失败（${response.status}），请重试。`)
  }
  if (!data) throw new Error('后端未返回 JSON，请检查代理配置。')
  return data
}

async function run(message, task) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  status.value = message
  try {
    await task()
  } catch (cause) {
    error.value = cause instanceof TypeError ? '网络连接失败，请检查连接后重试。' : cause.message
    status.value = '操作未完成，已保留现有练习和作答。'
  } finally {
    busy.value = false
  }
}

function makeCard(exercise, variantOf = null) {
  return { ...exercise, answer: '', supplement: false, attempts: [], next: null, variantOf }
}

function startPractice() {
  if (!goal.value.trim()) return
  run('正在创建练习…', async () => {
    const session = await post('/sessions', {})
    const exercise = await post('/exercises/generate', { session_id: session.session_id, goal: goal.value.trim() })
    // Commit the replacement only after both requests succeed.
    sessionId.value = session.session_id
    cards.value = [makeCard(exercise)]
    status.value = '题目已生成，请写下答案和理由。'
  })
}

function canAnswer(card) {
  return !card.next || ['retry', 'clarify'].includes(card.next.action)
}

function submitAnswer(card) {
  if (!sessionId.value || !canAnswer(card) || !card.answer.trim()) return
  run('正在批改作答…', async () => {
    const answer = card.answer
    const supplement = card.supplement
    const result = await post(`/exercises/${encodeURIComponent(card.exercise_id)}/attempts`, {
      session_id: sessionId.value, answer: answer.trim(), supplement,
    })
    card.attempts.push({ ...result, answer, supplement })
    card.next = result.next
    card.answer = ''
    card.supplement = false
    status.value = result.next.action === 'practice_complete'
      ? '练习完成！你已通过原题和变式。'
      : '批改完成，请查看反馈和下一步。'
  })
}

function generateNext(card) {
  if (!sessionId.value) return
  run('正在生成下一题…', async () => {
    const variantOf = card.next?.variant_of || card.variantOf
    const body = { session_id: sessionId.value }
    if (variantOf) body.variant_of = variantOf
    else body.goal = card.goal
    const exercise = await post('/exercises/generate', body)
    cards.value.push(makeCard(exercise, variantOf || null))
    status.value = '新题已生成，原题和历史作答已保留。'
  })
}
</script>

<template>
  <main :aria-busy="busy">
    <header>
      <p class="eyebrow">StudyFlow</p>
      <h1>引导式练习</h1>
      <p>写下目标 → 尝试作答 → 根据反馈改进 → 挑战变式</p>
    </header>

    <section class="card" aria-labelledby="goal-heading">
      <h2 id="goal-heading">今天想练习什么？</h2>
      <form @submit.prevent="startPractice">
        <label for="goal">学习目标</label>
        <textarea id="goal" v-model="goal" maxlength="500" rows="3" required :disabled="busy"
          placeholder="例如：练习用一元一次方程解决实际问题，并解释理由。"></textarea>
        <p class="muted">{{ goal.length }} / 500 · 开始新练习成功后，将替换当前练习。</p>
        <button type="submit" :disabled="busy || !goal.trim()">开始新练习</button>
      </form>
    </section>

    <p role="status" aria-live="polite" class="status">{{ status }}</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>

    <section v-for="(card, index) in cards" :key="card.exercise_id" class="card exercise"
      :aria-labelledby="`exercise-${index}`">
      <p class="eyebrow">第 {{ index + 1 }} 题 · {{ card.variantOf ? '巩固变式' : '目标练习' }}</p>
      <h2 :id="`exercise-${index}`">{{ card.goal }}</h2>
      <p class="preserve question">{{ card.question }}</p>

      <ol v-if="card.attempts.length" class="attempts" aria-label="作答记录">
        <li v-for="attempt in card.attempts" :key="attempt.attempt_no">
          <h3>第 {{ attempt.attempt_no }} 次作答{{ attempt.supplement ? '（补充）' : '' }}</h3>
          <p class="preserve">{{ attempt.answer }}</p>
          <p><strong>{{ statusLabels[attempt.assessment.status] || attempt.assessment.status }}</strong></p>
          <p class="preserve">{{ attempt.assessment.feedback }}</p>
          <p v-if="attempt.next.hint" class="preserve hint">提示：{{ attempt.next.hint }}</p>
        </li>
      </ol>

      <template v-if="index === cards.length - 1">
        <form v-if="canAnswer(card)" @submit.prevent="submitAnswer(card)">
          <fieldset :disabled="busy || !sessionId">
            <label :for="`answer-${index}`">{{ card.next?.action === 'clarify' ? '请澄清你的答案和理由' : '你的答案和理由' }}</label>
            <textarea :id="`answer-${index}`" v-model="card.answer" maxlength="4000" rows="5" required></textarea>
            <p class="muted">{{ card.answer.length }} / 4000</p>
            <label v-if="card.attempts.length" class="checkbox">
              <input v-model="card.supplement" type="checkbox" /> 补充上次作答（不勾选则重新作答）
            </label>
            <button type="submit" :disabled="busy || !sessionId || !card.answer.trim()">提交作答</button>
          </fieldset>
        </form>
        <button v-else-if="['generate_variant', 'regenerate'].includes(card.next?.action)"
          type="button" :disabled="busy || !sessionId" @click="generateNext(card)">
          {{ card.next.action === 'generate_variant' ? '生成巩固变式' : '重新生成题目' }}
        </button>
        <p v-else-if="card.next?.action === 'practice_complete'" class="complete">练习完成！你已通过原题和变式。</p>
      </template>
    </section>
  </main>
</template>
