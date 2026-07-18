import { useState } from 'react'
import { assetThumbnailUrl } from '../../api'

type AssetThumbnailProps = {
  assetId: string
}

export function AssetThumbnail({ assetId }: AssetThumbnailProps) {
  const [failed, setFailed] = useState(false)
  return (
    <div aria-hidden="true" className="asset-thumbnail">
      {!failed && <img alt="" onError={() => setFailed(true)} src={assetThumbnailUrl(assetId)} />}
    </div>
  )
}
