import { useCallback, useEffect, useRef, useState } from 'react'

/** The browser's own speech recognition, where it has one (Chrome, Edge, Safari). Typed loosely: it isn't in the DOM types. */
type Recognition = {
  lang: string
  interimResults: boolean
  continuous: boolean
  maxAlternatives: number
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
}

function recognitionClass(): (new () => Recognition) | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export const speechSupported = () => recognitionClass() !== null

const ERRORS: Record<string, string> = {
  'not-allowed': 'The microphone is blocked for this site. Allow it in your browser’s site settings, or type instead.',
  'service-not-allowed': 'Voice typing is switched off in this browser. Type instead.',
  'no-speech': 'I didn’t hear anything. Try again, a little closer.',
  'audio-capture': 'No microphone was found.',
  network: 'Voice typing needs a connection here. Type instead.',
}

/**
 * Push to talk. `listen` resolves with what was said once the person stops
 * (or after a pause); `heard` is the words so far, for showing as they come.
 */
export function useSpeech() {
  const [listening, setListening] = useState(false)
  const [heard, setHeard] = useState('')
  const active = useRef<Recognition | null>(null)

  const stop = useCallback(() => active.current?.stop(), [])
  /** Stop listening and forget what was heard: the person chose to type instead. */
  const cancel = useCallback(() => {
    const held = active.current
    active.current = null
    if (held) {
      held.onend = null
      held.abort()
    }
    setListening(false)
  }, [])
  useEffect(() => () => active.current?.abort(), [])

  const listen = useCallback(
    () =>
      new Promise<string>((resolve, reject) => {
        const Class = recognitionClass()
        if (!Class) {
          reject(new Error('This browser has no voice typing. Type what you would say.'))
          return
        }
        active.current?.abort()
        const recognition = new Class()
        recognition.lang = navigator.language || 'en-US'
        recognition.interimResults = true
        recognition.continuous = false
        recognition.maxAlternatives = 1
        let final = ''
        let failed: string | null = null
        recognition.onresult = (event) => {
          let text = ''
          for (let i = 0; i < event.results.length; i++) text += event.results[i][0].transcript
          final = text
          setHeard(text)
        }
        recognition.onerror = (event) => {
          failed = ERRORS[event.error] ?? 'Voice typing stopped. Try again, or type instead.'
        }
        recognition.onend = () => {
          setListening(false)
          active.current = null
          if (final.trim()) resolve(final.trim())
          else reject(new Error(failed ?? ERRORS['no-speech']))
        }
        active.current = recognition
        setHeard('')
        setListening(true)
        try {
          recognition.start()
        } catch {
          setListening(false)
          reject(new Error('Voice typing could not start. Try again.'))
        }
      }),
    [],
  )

  return { supported: speechSupported(), listening, heard, listen, stop, cancel }
}

/** Say something aloud, if the browser can. Resolves when it has finished (or straight away if it can't). */
export function say(text: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      if (!('speechSynthesis' in window)) return resolve()
      window.speechSynthesis.cancel()
      const utterance = new SpeechSynthesisUtterance(text)
      utterance.rate = 1.05
      utterance.onend = () => resolve()
      utterance.onerror = () => resolve()
      window.speechSynthesis.speak(utterance)
      // Some browsers never fire onend when the tab is in the background.
      window.setTimeout(resolve, Math.min(12000, 1500 + text.length * 90))
    } catch {
      resolve()
    }
  })
}

const ONES: Record<string, number> = { zero: 0, oh: 0, one: 1, two: 2, to: 2, too: 2, three: 3, four: 4, for: 4, five: 5, six: 6, seven: 7, eight: 8, ate: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19 }
const TENS: Record<string, number> = { twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 }

/** A lone amount, as said in count mode: "16", "sixteen", "twenty five", "two and a half", "half", "none". Null if it isn't one. */
export function spokenNumber(text: string): number | null {
  const words = text.toLowerCase().replace(/-/g, ' ').match(/\d+(\.\d+)?|[a-z]+/g) ?? []
  if (words.length === 0) return null
  if (words.length === 1 && ['none', 'no', 'nothing', 'empty', 'out'].includes(words[0])) return 0
  let value = 0
  let any = false
  for (let i = 0; i < words.length; i++) {
    const w = words[i]
    if (/^\d/.test(w)) {
      value += Number(w)
      any = true
    } else if (w in ONES || w in TENS) {
      value += ONES[w] ?? TENS[w]
      any = true
    } else if (w === 'hundred') {
      value = (value || 1) * 100
      any = true
    } else if (w === 'half') {
      value += 0.5
      any = true
    } else if (w === 'quarter') {
      value += 0.25
      any = true
    } else if (w === 'point' && i + 1 < words.length) {
      const digits = words.slice(i + 1).map((d) => (d in ONES ? String(ONES[d]) : /^\d+$/.test(d) ? d : '')).join('')
      if (digits) return value + Number(`0.${digits}`)
    } else if (!['and', 'a', 'an', 'about', 'pounds', 'pound', 'lb', 'lbs', 'bunches', 'bunch', 'heads', 'head', 'cases', 'case', 'loaves', 'loaf', 'liters', 'bags', 'each'].includes(w)) {
      return null
    }
  }
  return any ? value : null
}
