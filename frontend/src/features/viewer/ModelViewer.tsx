import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AmbientLight,
  AxesHelper,
  Box3,
  DirectionalLight,
  GridHelper,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  Scene,
  Vector3,
  WebGLRenderer,
} from 'three'
import type { Material, Object3D } from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { qualifyThreeMfProductionPaths } from './threeMfProductionPaths'
import { selectModelLoader, validateViewerSource, type ValidViewerSource, type ViewerSource } from './viewerSource'
import './model-viewer.css'

type ViewerStatus = 'loading' | 'ready' | 'parse-failure'

type Runtime = {
  axes: AxesHelper
  controls: OrbitControls
  grid: GridHelper
  materials: Material[]
  resetView: () => void
}

type DisposableObject = Object3D & {
  geometry?: { dispose: () => void }
  material?: Material | Material[]
}

export type ModelViewerProps = {
  source: ViewerSource | null
  buildColors?: readonly (string | null)[]
}

function disposeMaterial(material: Material): void {
  for (const value of Object.values(material)) {
    const texture = value as { isTexture?: boolean; dispose?: () => void } | null
    if (texture?.isTexture && typeof texture.dispose === 'function') {
      texture.dispose()
    }
  }
  material.dispose()
}

export function disposeObjectResources(root: Object3D): void {
  root.traverse((item) => {
    const object = item as DisposableObject
    object.geometry?.dispose()
    const materials = object.material ? (Array.isArray(object.material) ? object.material : [object.material]) : []
    materials.forEach(disposeMaterial)
  })
}

function updateMaterialWireframe(material: Material, wireframe: boolean): void {
  if (!('wireframe' in material)) return

  const wireframeMaterial = material as Material & { wireframe: boolean }
  wireframeMaterial.wireframe = wireframe
  material.needsUpdate = true
}

function isHexColor(value: string | null | undefined): value is string {
  return typeof value === 'string' && /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i.test(value)
}

function cloneMaterialWithColor(material: Material, color: string): Material {
  if (!('color' in material)) return material
  const clone = material.clone()
  const colorMaterial = clone as Material & { color?: { set: (value: string) => void } }
  colorMaterial.color?.set(color)
  clone.needsUpdate = true
  return clone
}

function applyBuildColor(buildItem: Object3D, color: string): void {
  buildItem.traverse((item) => {
    if (!(item instanceof Mesh)) return
    item.material = Array.isArray(item.material)
      ? item.material.map((material) => cloneMaterialWithColor(material, color))
      : cloneMaterialWithColor(item.material, color)
  })
}

export function applyPreviewMaterial(
  root: Object3D,
  wireframe: boolean,
  buildColors: readonly (string | null)[] = [],
): Material[] {
  root.children.forEach((buildItem, index) => {
    const color = buildColors[index]
    if (isHexColor(color)) applyBuildColor(buildItem, color)
  })
  const materials: Material[] = []
  root.traverse((item) => {
    if (!(item instanceof Mesh)) return

    const existingMaterials = Array.isArray(item.material) ? item.material : [item.material]
    existingMaterials.forEach((material) => {
      updateMaterialWireframe(material, wireframe)
      materials.push(material)
    })
  })
  return materials
}

export function loadModel(source: ValidViewerSource, onLoad: (root: Object3D) => void, onFailure: () => void): void {
  const onError = () => onFailure()

  switch (selectModelLoader(source.format)) {
    case 'stl': {
      void import('three/addons/loaders/STLLoader.js').then(({ STLLoader }) => {
        const loader = new STLLoader()
        loader.setWithCredentials(true)
        loader.load(source.url, (geometry) => {
          geometry.computeVertexNormals()
          onLoad(new Mesh(geometry, new MeshStandardMaterial({ color: 0x8b81f5 })))
        }, undefined, onError)
      }).catch(onError)
      return
    }
    case 'obj': {
      void import('three/addons/loaders/OBJLoader.js').then(({ OBJLoader }) => {
        const loader = new OBJLoader()
        loader.setWithCredentials(true)
        loader.load(source.url, onLoad, undefined, onError)
      }).catch(onError)
      return
    }
    case '3mf': {
      void import('three/addons/loaders/3MFLoader.js').then(({ ThreeMFLoader }) => {
        const loader = new ThreeMFLoader()
        void fetch(source.url, { credentials: 'include' })
          .then((response) => {
            if (!response.ok) throw new Error(`3MF download failed: ${response.status}`)
            return response.arrayBuffer()
          })
          .then((archive) => {
            onLoad(loader.parse(qualifyThreeMfProductionPaths(archive)))
          })
          .catch(onError)
      }).catch(onError)
      return
    }
  }
}

function fitToView(root: Object3D, camera: PerspectiveCamera, controls: OrbitControls): () => void {
  const box = new Box3().setFromObject(root)
  if (box.isEmpty()) throw new Error('Empty model')

  const center = box.getCenter(new Vector3())
  const size = box.getSize(new Vector3())
  const radius = Math.max(size.x, size.y, size.z, 1) / 2
  root.position.sub(center)

  return () => {
    const distance = radius / Math.tan((camera.fov * Math.PI) / 360) * 1.25
    camera.near = Math.max(radius / 100, 0.01)
    camera.far = Math.max(distance * 20, 1_000)
    camera.position.set(distance, distance * 0.72, distance)
    camera.updateProjectionMatrix()
    controls.target.set(0, 0, 0)
    controls.update()
  }
}

export function ModelViewer({ source, buildColors = [] }: ModelViewerProps) {
  const { t } = useTranslation()
  const canvasHostRef = useRef<HTMLDivElement>(null)
  const expandButtonRef = useRef<HTMLButtonElement>(null)
  const expandedCloseButtonRef = useRef<HTMLButtonElement>(null)
  const expandedViewerRef = useRef<HTMLElement>(null)
  const runtimeRef = useRef<Runtime | null>(null)
  const [showGrid, setShowGrid] = useState(true)
  const [showAxes, setShowAxes] = useState(false)
  const [wireframe, setWireframe] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const settingsRef = useRef({ showGrid, showAxes, wireframe })
  const [status, setStatus] = useState<ViewerStatus>('loading')
  const validation = useMemo(() => validateViewerSource(source), [source])
  const buildColorSignature = buildColors.join('|')

  useEffect(() => {
    const objectUrl = source?.kind === 'object-url' && source.url.startsWith('blob:') ? source.url : null
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [source])

  useEffect(() => {
    settingsRef.current.showGrid = showGrid
    const runtime = runtimeRef.current
    if (runtime) runtime.grid.visible = showGrid
  }, [showGrid])

  useEffect(() => {
    settingsRef.current.showAxes = showAxes
    const runtime = runtimeRef.current
    if (runtime) runtime.axes.visible = showAxes
  }, [showAxes])

  useEffect(() => {
    settingsRef.current.wireframe = wireframe
    const runtime = runtimeRef.current
    runtime?.materials.forEach((material) => {
      updateMaterialWireframe(material, wireframe)
    })
  }, [wireframe])

  useEffect(() => {
    if (!expanded) return undefined
    window.requestAnimationFrame(() => expandedCloseButtonRef.current?.focus())
    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setExpanded(false)
        window.requestAnimationFrame(() => expandButtonRef.current?.focus())
        return
      }
      if (event.key !== 'Tab') return
      const focusable = expandedViewerRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])')
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handleKeyboard)
    return () => window.removeEventListener('keydown', handleKeyboard)
  }, [expanded])

  useEffect(() => {
    if (!validation.ok || !canvasHostRef.current) return undefined

    const host = canvasHostRef.current
    let cancelled = false
    let animationFrame = 0
    let root: Object3D | null = null
    setStatus('loading')

    let renderer: WebGLRenderer
    try {
      renderer = new WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      setStatus('parse-failure')
      return undefined
    }

    const scene = new Scene()
    const camera = new PerspectiveCamera(45, 1, 0.01, 10_000)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    scene.add(new AmbientLight(0xffffff, 1.7))
    const keyLight = new DirectionalLight(0xffffff, 2.4)
    keyLight.position.set(5, 8, 7)
    scene.add(keyLight)

    const grid = new GridHelper(100, 20, 0x5e6ad2, 0x334155)
    grid.rotation.x = Math.PI / 2
    const axes = new AxesHelper(50)
    grid.visible = settingsRef.current.showGrid
    axes.visible = settingsRef.current.showAxes
    scene.add(grid, axes)
    host.appendChild(renderer.domElement)

    const resize = () => {
      const bounds = host.getBoundingClientRect()
      const width = Math.max(Math.round(bounds.width), 1)
      const height = Math.max(Math.round(bounds.height), 1)
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }
    resize()
    window.addEventListener('resize', resize)
    const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize)
    resizeObserver?.observe(host)

    const render = () => {
      controls.update()
      renderer.render(scene, camera)
      animationFrame = window.requestAnimationFrame(render)
    }
    render()

    loadModel(validation.source, (loadedRoot) => {
      if (cancelled) {
        disposeObjectResources(loadedRoot)
        return
      }

      try {
        root = loadedRoot
        const materials = applyPreviewMaterial(loadedRoot, settingsRef.current.wireframe, buildColors)
        scene.add(loadedRoot)
        const resetView = fitToView(loadedRoot, camera, controls)
        resetView()
        runtimeRef.current = { axes, controls, grid, materials, resetView }
        setStatus('ready')
      } catch {
        if (root) {
          scene.remove(root)
          disposeObjectResources(root)
          root = null
        }
        setStatus('parse-failure')
      }
    }, () => {
      if (!cancelled) setStatus('parse-failure')
    })
    return () => {
      cancelled = true
      runtimeRef.current = null
      window.cancelAnimationFrame(animationFrame)
      window.removeEventListener('resize', resize)
      resizeObserver?.disconnect()
      controls.dispose()
      if (root) disposeObjectResources(root)
      disposeObjectResources(grid)
      disposeObjectResources(axes)
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [buildColorSignature, source, validation])

  if (!source) return null

  const errorKey = !validation.ok
    ? validation.reason === 'oversized' ? 'viewer.oversized' : 'viewer.unsupported'
    : status === 'parse-failure' ? 'viewer.parseFailure' : null

  const closeExpanded = () => {
    setExpanded(false)
    window.requestAnimationFrame(() => expandButtonRef.current?.focus())
  }

  return (
    <>
      {expanded && <button aria-label={t('viewer.closeExpanded')} className="model-viewer-backdrop" onClick={closeExpanded} type="button" />}
      <section aria-labelledby={expanded ? 'model-viewer-dialog-title' : undefined} aria-modal={expanded || undefined} className={`model-viewer${expanded ? ' is-expanded' : ''}`} ref={expandedViewerRef} role={expanded ? 'dialog' : undefined}>
      {expanded && <header className="model-viewer-expanded-header"><h2 id="model-viewer-dialog-title">{t('viewer.expandedTitle')}</h2><button onClick={closeExpanded} ref={expandedCloseButtonRef} type="button">{t('viewer.closeExpanded')}</button></header>}
      {validation.ok && <div className="model-viewer-canvas" ref={canvasHostRef} />}
      {validation.ok && status === 'loading' && <p className="model-viewer-status" role="status">{t('viewer.loading')}</p>}
      {errorKey && <p className="model-viewer-status is-error" role="alert">{t(errorKey)}</p>}
      {validation.ok && status === 'ready' && (
        <div className="model-viewer-controls" role="group">
          {status === 'ready' && <><button onClick={() => runtimeRef.current?.resetView()} type="button">{t('viewer.resetView')}</button><button aria-pressed={wireframe} onClick={() => setWireframe((current) => !current)} type="button">{t('viewer.wireframe')}</button><button aria-pressed={showGrid} onClick={() => setShowGrid((current) => !current)} type="button">{t('viewer.grid')}</button><button aria-pressed={showAxes} onClick={() => setShowAxes((current) => !current)} type="button">{t('viewer.axes')}</button></>}
          <button aria-pressed={expanded} onClick={() => setExpanded((current) => !current)} ref={expandButtonRef} type="button">{t('viewer.expand')}</button>
        </div>
      )}
      </section>
    </>
  )
}
