import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { api, ApiError } from '../api/client'
import { Badge } from './Badge'

const ACCEPT = '.mp3,.m4a,.wav,.aac,.flac,audio/*'
const ACCEPT_EXT = ['mp3', 'm4a', 'wav', 'aac', 'flac']

function isAccepted(file: File): boolean {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (ACCEPT_EXT.includes(ext)) return true
  return file.type.startsWith('audio/')
}

function stripExtension(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx > 0 ? name.slice(0, idx) : name
}

interface UploadPanelProps {
  onCreated: (interviewId: string) => void
}

export function UploadPanel({ onCreated }: UploadPanelProps) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [company, setCompany] = useState('')
  const [position, setPosition] = useState('')
  const [dragging, setDragging] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)

  function pickFile(next: File | null) {
    if (!next) return
    if (!isAccepted(next)) {
      setError('仅支持 mp3 / m4a / wav / aac / flac 格式的音频文件。')
      return
    }
    setError(null)
    setFile(next)
    if (!title.trim()) setTitle(stripExtension(next.name))
  }

  function reset() {
    setFile(null)
    setTitle('')
    setCompany('')
    setPosition('')
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  async function handleSubmit() {
    if (!file || !title.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    const form = new FormData()
    form.append('file', file)
    form.append('title', title.trim())
    if (company.trim()) form.append('company', company.trim())
    if (position.trim()) form.append('position', position.trim())
    try {
      const result = await api.createInterview(form)
      reset()
      onCreated(result.interview_id)
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : '上传失败，请稍后重试。',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">上传新面试录音</h2>
        <span className="text-xs text-neutral-400">
          支持 mp3 / m4a / wav / aac / flac
        </span>
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_320px] gap-5">
        {/* 拖拽区 */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              inputRef.current?.click()
            }
          }}
          onDragEnter={(e) => {
            e.preventDefault()
            dragDepth.current += 1
            setDragging(true)
          }}
          onDragOver={(e) => e.preventDefault()}
          onDragLeave={(e) => {
            e.preventDefault()
            dragDepth.current -= 1
            if (dragDepth.current <= 0) setDragging(false)
          }}
          onDrop={(e) => {
            e.preventDefault()
            dragDepth.current = 0
            setDragging(false)
            const dropped = e.dataTransfer.files?.[0]
            if (dropped) pickFile(dropped)
          }}
          className={`flex min-h-[168px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 text-center transition-colors ${
            dragging
              ? 'border-blue-400 bg-blue-50/60'
              : file
                ? 'border-emerald-300 bg-emerald-50/40'
                : 'border-neutral-300 bg-neutral-50/60 hover:border-neutral-400'
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-neutral-800">
                {file.name}
              </p>
              <p className="mt-1 text-xs text-neutral-500">
                {(file.size / 1024 / 1024).toFixed(1)} MB · 点击可重新选择
              </p>
            </div>
          ) : (
            <>
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                className="mb-2 size-8 text-neutral-400"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 16V4m0 0L8 8m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"
                />
              </svg>
              <p className="text-sm font-medium text-neutral-700">
                拖拽音频到此处，或点击选择文件
              </p>
              <p className="mt-1 text-xs text-neutral-400">
                上传后会自动开始八个阶段的复盘流程
              </p>
            </>
          )}
        </div>

        {/* 表单 */}
        <div className="flex flex-col gap-3">
          <Field label="标题" required>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：字节跳动 后端二面"
              className="w-full rounded-md border border-neutral-300 bg-white px-2.5 py-1.5 text-sm outline-none placeholder:text-neutral-400 focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="公司">
              <input
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="可选"
                className="w-full rounded-md border border-neutral-300 bg-white px-2.5 py-1.5 text-sm outline-none placeholder:text-neutral-400 focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
              />
            </Field>
            <Field label="岗位">
              <input
                value={position}
                onChange={(e) => setPosition(e.target.value)}
                placeholder="可选"
                className="w-full rounded-md border border-neutral-300 bg-white px-2.5 py-1.5 text-sm outline-none placeholder:text-neutral-400 focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
              />
            </Field>
          </div>
          <button
            type="button"
            disabled={!file || !title.trim() || submitting}
            onClick={handleSubmit}
            className="mt-auto inline-flex h-9 items-center justify-center gap-2 rounded-md bg-neutral-900 px-4 text-sm font-medium text-white transition-colors hover:bg-neutral-800 disabled:cursor-not-allowed disabled:bg-neutral-300"
          >
            {submitting && (
              <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            )}
            {submitting ? '上传中…' : '上传并开始复盘'}
          </button>
        </div>
      </div>

      {error && (
        <p className="mt-3 flex items-center gap-2 text-xs text-red-600">
          <Badge tone="red">错误</Badge>
          {error}
        </p>
      )}
    </section>
  )
}

function Field({
  label,
  required,
  children,
}: {
  label: string
  required?: boolean
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-neutral-500">
        {label}
        {required && <span className="ml-0.5 text-red-500">*</span>}
      </span>
      {children}
    </label>
  )
}
