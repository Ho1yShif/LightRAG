import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { SearchIcon, XIcon } from 'lucide-react'
import { useAuthStore } from '@/stores/state'

/**
 * Dismissible banner shown only in read-only public demo mode. Signals that
 * uploading/editing are disabled while browsing and querying stay available.
 */
const DemoBanner = () => {
  const { t } = useTranslation()
  const demoMode = useAuthStore((state) => state.demoMode)
  const [dismissed, setDismissed] = useState(false)

  if (!demoMode || dismissed) {
    return null
  }

  return (
    <div className="flex items-center justify-center gap-2 border-b border-amber-300 bg-amber-100 px-4 py-1.5 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
      <SearchIcon className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="text-center">
        {t(
          'demoBanner.message',
          'Read-only demo — explore and query the knowledge graph; uploading and editing are disabled.'
        )}
      </span>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="ml-1 shrink-0 rounded p-0.5 hover:bg-amber-200 dark:hover:bg-amber-900"
        aria-label={t('demoBanner.dismiss', 'Dismiss')}
      >
        <XIcon className="size-3.5" aria-hidden="true" />
      </button>
    </div>
  )
}

export default DemoBanner
