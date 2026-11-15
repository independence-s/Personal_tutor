import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import App from './App.vue'

let wrapper
let fetchMock
const exercise = (id = 'original', question = '解方程：2x = 6，并说明理由。') => ({
  exercise_id: id, goal: '练习方程', question,
})
const attempt = (status, action, attempt_no = 1, extra = {}) => ({
  attempt_no, assessment: { status, feedback: '请解释每一步。' }, next: { action, ...extra },
})
function respond(data, status = 201) {
  fetchMock.mockResolvedValueOnce({ ok: status >= 200 && status < 300, status, json: async () => data })
}
function body(index) { return JSON.parse(fetchMock.mock.calls[index][1].body) }
function button(text) { return wrapper.findAll('button').find((item) => item.text() === text) }
async function start() {
  respond({ session_id: 'session-1' })
  respond(exercise())
  await wrapper.get('#goal').setValue('练习方程')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}
async function answer(text) {
  await wrapper.get('.exercise textarea').setValue(text)
  await wrapper.get('.exercise form').trigger('submit')
  await flushPromises()
}

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  wrapper = mount(App)
})
afterEach(() => {
  wrapper.unmount()
  vi.unstubAllGlobals()
})

describe('guided practice', () => {
  it('creates a session and generates a question with bounded inputs', async () => {
    expect(wrapper.get('#goal').attributes('maxlength')).toBe('500')
    expect(button('开始新练习').attributes('disabled')).toBeDefined()
    await start()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/sessions', '/api/exercises/generate'])
    expect(body(1)).toEqual({ session_id: 'session-1', goal: '练习方程' })
    expect(wrapper.get('.question').text()).toBe(exercise().question)
    expect(wrapper.get('.exercise textarea').attributes('maxlength')).toBe('4000')
    expect(wrapper.find('input[type=checkbox]').exists()).toBe(false)
    expect(wrapper.get('[role=status]').text()).toContain('题目已生成')
  })

  it('preserves the draft and cards on failed submissions, including network failures', async () => {
    await start()
    respond({ detail: '模型调用失败，请重试' }, 502)
    await answer('x = 3，因为两边除以 2。')
    expect(wrapper.get('[role=alert]').text()).toContain('模型调用失败')
    expect(wrapper.get('.exercise textarea').element.value).toContain('x = 3')
    expect(wrapper.findAll('.attempts li')).toHaveLength(0)
    expect(wrapper.findAll('.exercise')).toHaveLength(1)
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await wrapper.get('.exercise form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('网络连接失败')
    expect(wrapper.get('.exercise textarea').element.value).toContain('x = 3')
  })

  it('allows retry and clarification, sends supplements, and only clears a successful draft', async () => {
    await start()
    respond(attempt('partial', 'retry', 1, { hint: '等式两边做同样的操作。' }))
    await answer('x = 3')
    expect(body(2)).toEqual({ session_id: 'session-1', answer: 'x = 3', supplement: false })
    expect(wrapper.text()).toContain('部分正确')
    expect(wrapper.text()).toContain('等式两边做同样的操作')
    expect(wrapper.get('.exercise textarea').element.value).toBe('')
    await wrapper.get('input[type=checkbox]').setValue(true)
    respond({ detail: '暂时无法批改' }, 503)
    await answer('两边除以 2')
    expect(wrapper.get('input[type=checkbox]').element.checked).toBe(true)
    expect(wrapper.get('.exercise textarea').element.value).toBe('两边除以 2')
    respond(attempt('needs_clarification', 'clarify', 2))
    await wrapper.get('.exercise form').trigger('submit')
    await flushPromises()
    expect(body(4).supplement).toBe(true)
    expect(wrapper.text()).toContain('需要澄清')
    expect(wrapper.text()).toContain('请澄清你的答案和理由')
    expect(wrapper.get('.exercise textarea').element.value).toBe('')
    expect(wrapper.get('input[type=checkbox]').element.checked).toBe(false)
  })

  it('completes the original and variant without losing the original or its attempts', async () => {
    await start()
    respond(attempt('correct', 'generate_variant', 1, { variant_of: 'original' }))
    await answer('x = 3，两边同除以 2。')
    expect(wrapper.text()).toContain('回答正确')
    expect(wrapper.find('.exercise textarea').exists()).toBe(false)
    respond(exercise('variant', '解方程：3x = 12，并说明理由。'))
    await button('生成巩固变式').trigger('click')
    await flushPromises()
    expect(body(3)).toEqual({ session_id: 'session-1', variant_of: 'original' })
    expect(wrapper.findAll('.exercise')).toHaveLength(2)
    expect(wrapper.findAll('.exercise form')).toHaveLength(1)
    expect(wrapper.findAll('.exercise')[0].text()).toContain('x = 3，两边同除以 2。')
    expect(wrapper.findAll('.exercise')[0].find('button').exists()).toBe(false)
    respond(attempt('correct', 'practice_complete'))
    await answer('x = 4，两边同除以 3。')
    expect(wrapper.get('.complete').text()).toContain('练习完成')
    expect(wrapper.findAll('.exercise')).toHaveLength(2)
    expect(wrapper.find('.exercise form').exists()).toBe(false)
  })

  it('regenerates invalid originals by goal and invalid variants by the original id', async () => {
    await start()
    respond(attempt('invalid_exercise', 'regenerate'))
    await answer('条件不完整')
    expect(wrapper.text()).toContain('题目无效')
    respond(exercise('replacement'))
    await button('重新生成题目').trigger('click')
    await flushPromises()
    expect(body(3)).toEqual({ session_id: 'session-1', goal: '练习方程' })
    respond(attempt('correct', 'generate_variant', 1, { variant_of: 'replacement' }))
    await answer('x = 3，两边同除以 2。')
    respond(exercise('variant'))
    await button('生成巩固变式').trigger('click')
    await flushPromises()
    respond(attempt('invalid_exercise', 'regenerate'))
    await answer('变式有误')
    respond(exercise('variant-replacement'))
    await button('重新生成题目').trigger('click')
    await flushPromises()
    expect(body(7)).toEqual({ session_id: 'session-1', variant_of: 'replacement' })
    expect(wrapper.findAll('.exercise')).toHaveLength(4)
  })

  it('preserves an old practice on failed starts, and creates a new session on every start', async () => {
    await start()
    await wrapper.get('.exercise textarea').setValue('尚未提交的答案')
    respond({ session_id: 'session-2' })
    respond({ detail: '生成失败' }, 502)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.exercise textarea').element.value).toBe('尚未提交的答案')
    // The previous session is still attached to the old card.
    respond(attempt('incorrect', 'retry'))
    await wrapper.get('.exercise form').trigger('submit')
    await flushPromises()
    expect(body(4).session_id).toBe('session-1')
    expect(wrapper.text()).toContain('回答有误')
    respond({ session_id: 'session-3' })
    respond(exercise('new', '新的题目'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/sessions')).toHaveLength(3)
    expect(wrapper.findAll('.exercise')).toHaveLength(1)
    expect(wrapper.get('.question').text()).toBe('新的题目')
    expect(wrapper.find('.attempts').exists()).toBe(false)
  })

  it('resets expired sessions on 404 while preserving the draft and prompting a new practice', async () => {
    await start()
    respond({ detail: '会话不存在' }, 404)
    await answer('保留我的答案')
    expect(wrapper.get('[role=alert]').text()).toContain('开始新练习')
    expect(wrapper.get('.exercise textarea').element.value).toBe('保留我的答案')
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
    expect(button('提交作答').attributes('disabled')).toBeDefined()
    expect(button('开始新练习').attributes('disabled')).toBeUndefined()
  })

  it('disables all controls while a request is in flight and ignores duplicate submits', async () => {
    let resolveSession
    fetchMock.mockImplementationOnce(() => new Promise((resolve) => { resolveSession = resolve }))
    await wrapper.get('#goal').setValue('练习方程')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('main').attributes('aria-busy')).toBe('true')
    expect(wrapper.get('#goal').attributes('disabled')).toBeDefined()
    expect(button('开始新练习').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    respond(exercise())
    resolveSession({ ok: true, status: 201, json: async () => ({ session_id: 'session-1' }) })
    await flushPromises()
    expect(wrapper.get('main').attributes('aria-busy')).toBe('false')
  })

  it('preserves cards when generation of a variant fails and permits retry', async () => {
    await start()
    respond(attempt('correct', 'generate_variant', 1, { variant_of: 'original' }))
    await answer('x = 3，因为两边除以 2。')
    respond({ detail: '模型重复了原题，请重新生成变式' }, 502)
    await button('生成巩固变式').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.exercise')).toHaveLength(1)
    expect(wrapper.get('.attempts').text()).toContain('x = 3')
    expect(button('生成巩固变式').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[role=alert]').text()).toContain('重新生成变式')
  })
})
