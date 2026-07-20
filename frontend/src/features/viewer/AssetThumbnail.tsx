import { useEffect, useRef, useState } from 'react'
import { assetThumbnailUrl } from '../../api'

type AssetThumbnailProps = {
  assetId: string
  revision?: number
}

export function AssetThumbnail({ assetId, revision = 0 }: AssetThumbnailProps) {
  const [failed, setFailed] = useState(false)
  const [shouldLoad, setShouldLoad] = useState(() => typeof window.IntersectionObserver === 'undefined')
  const container = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setFailed(false)
    if (typeof window.IntersectionObserver === 'undefined' || !container.current) {
      setShouldLoad(true)
      return undefined
    }
    setShouldLoad(false)
    const observer = new window.IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      setShouldLoad(true)
      observer.disconnect()
    }, { rootMargin: '480px 0px' })
    observer.observe(container.current)
    return () => observer.disconnect()
  }, [assetId, revision])

  return (
    <div aria-hidden="true" className="asset-thumbnail" ref={container}>
      {shouldLoad && !failed && <img alt="" loading="lazy" onError={() => setFailed(true)} src={`${assetThumbnailUrl(assetId)}?revision=${revision}`} />}
    </div>
  )
}
