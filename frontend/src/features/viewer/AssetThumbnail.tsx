import { useEffect, useMemo, useRef, useState } from 'react'
import { AmbientLight, Box3, DirectionalLight, PerspectiveCamera, Scene, Vector3, WebGLRenderer } from 'three'
import type { Object3D } from 'three'
import { applyPreviewMaterial, disposeObjectResources, loadModel } from './ModelViewer'
import { validateViewerSource, type ViewerSource } from './viewerSource'

type AssetThumbnailProps = {
  source: ViewerSource
}

function frameObject(root: Object3D, camera: PerspectiveCamera): void {
  const box = new Box3().setFromObject(root)
  if (box.isEmpty()) throw new Error('Empty model')
  const center = box.getCenter(new Vector3())
  const size = box.getSize(new Vector3())
  const radius = Math.max(size.x, size.y, size.z, 1) / 2
  root.position.sub(center)
  const distance = radius / Math.tan((camera.fov * Math.PI) / 360) * 1.35
  camera.near = Math.max(radius / 100, 0.01)
  camera.far = Math.max(distance * 20, 1_000)
  camera.position.set(distance, distance * 0.7, distance)
  camera.lookAt(0, 0, 0)
  camera.updateProjectionMatrix()
}

export function AssetThumbnail({ source }: AssetThumbnailProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(typeof IntersectionObserver === 'undefined')
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const validation = useMemo(() => validateViewerSource(source), [source.byteSize, source.format, source.kind, source.url])

  useEffect(() => {
    const host = hostRef.current
    if (!host || typeof IntersectionObserver === 'undefined') return undefined
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return
      setVisible(true)
      observer.disconnect()
    }, { rootMargin: '160px' })
    observer.observe(host)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!visible || !validation.ok) return undefined
    let cancelled = false
    let root: Object3D | null = null
    let renderer: WebGLRenderer | null = null
    try {
      renderer = new WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: true })
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
      renderer.setSize(320, 220, false)
    } catch {
      return undefined
    }
    const scene = new Scene()
    const camera = new PerspectiveCamera(42, 320 / 220, 0.01, 10_000)
    scene.add(new AmbientLight(0xffffff, 1.8))
    const keyLight = new DirectionalLight(0xffffff, 2.2)
    keyLight.position.set(5, 7, 6)
    scene.add(keyLight)

    loadModel(validation.source, (loadedRoot) => {
      if (cancelled) {
        disposeObjectResources(loadedRoot)
        return
      }
      try {
        root = loadedRoot
        applyPreviewMaterial(loadedRoot, false)
        scene.add(loadedRoot)
        frameObject(loadedRoot, camera)
        renderer?.render(scene, camera)
        if (!cancelled) setImageUrl(renderer?.domElement.toDataURL('image/png') ?? null)
      } catch {
        if (root) disposeObjectResources(root)
      } finally {
        renderer?.dispose()
        renderer = null
      }
    }, () => {
      renderer?.dispose()
      renderer = null
    })

    return () => {
      cancelled = true
      if (root) disposeObjectResources(root)
      renderer?.dispose()
    }
  }, [validation, visible])

  return <div aria-hidden="true" className="asset-thumbnail" ref={hostRef}>{imageUrl && <img alt="" src={imageUrl} />}</div>
}
