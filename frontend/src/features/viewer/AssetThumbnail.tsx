import { useEffect, useState } from 'react'
import { assetThumbnailUrl } from '../../api'

type AssetThumbnailProps = {
  assetId: string
  revision?: number
}

export function AssetThumbnail({ assetId, revision = 0 }: AssetThumbnailProps) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [assetId, revision])
  return (
    <div aria-hidden="true" className="asset-thumbnail">
      {!failed && <img alt="" onError={() => setFailed(true)} src={`${assetThumbnailUrl(assetId)}?revision=${revision}`} />}
    </div>
  )
}
