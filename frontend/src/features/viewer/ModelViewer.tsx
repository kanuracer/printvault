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
import { selectModelLoader, validateViewerSource, type ValidViewerSource, type ViewerSource } from './viewerSource'
import './model-viewer.css'

type ViewerStatus = 'loading' | 'ready' | 'parse-failure'

type Runtime = {
  axes: AxesHelper
  controls: OrbitControls
  grid: GridHelper
  materials: MeshStandardMaterial[]
  resetView: () => void
}

type DisposableObject = Object3D & {
  geometry?: { dispose: () => void }
  material?: Material | Material[]
}

export type ModelViewerProps = {
  source: ViewerSource | null
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

export function applyPreviewMaterial(root: Object3D, wireframe: boolean): MeshStandardMaterial[] {
  const materials: MeshStandardMaterial[] = []
  root.traverse((item) => {
    if (!(item instanceof Mesh)) return

    const existingMaterials = Array.isArray(item.material) ? item.material : [item.material]
    existingMaterials.forEach(disposeMaterial)

    const material = new MeshStandardMaterial({ color: 0x8b81f5, metalness: 0.1, roughness: 0.58, wireframe })
    item.material = material
    materials.push(material)
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
          onLoad(new Mesh(geometry, new MeshStandardMaterial()))
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
        loader.setWithCredentials(true)
        loader.load(source.url, onLoad, undefined, onError)
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

export function ModelViewer({ source }: ModelViewerProps) {
  const { t } = useTranslation()
  const canvasHostRef = useRef<HTMLDivElement>(null)
  const runtimeRef = useRef<Runtime | null>(null)
  const [showGrid, setShowGrid] = useState(true)
  const [showAxes, setShowAxes] = useState(false)
  const [wireframe, setWireframe] = useState(false)
  const settingsRef = useRef({ showGrid, showAxes, wireframe })
  const [status, setStatus] = useState<ViewerStatus>('loading')
  const validation = useMemo(() => validateViewerSource(source), [source])

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
      material.wireframe = wireframe
      material.needsUpdate = true
    })
  }, [wireframe])

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
        const materials = applyPreviewMaterial(loadedRoot, settingsRef.current.wireframe)
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
      controls.dispose()
      if (root) disposeObjectResources(root)
      disposeObjectResources(grid)
      disposeObjectResources(axes)
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [source, validation])

  if (!source) return null

  const errorKey = !validation.ok
    ? validation.reason === 'oversized' ? 'viewer.oversized' : 'viewer.unsupported'
    : status === 'parse-failure' ? 'viewer.parseFailure' : null

  return (
    <section className="model-viewer">
      {validation.ok && <div className="model-viewer-canvas" ref={canvasHostRef} />}
      {validation.ok && status === 'loading' && <p className="model-viewer-status" role="status">{t('viewer.loading')}</p>}
      {errorKey && <p className="model-viewer-status is-error" role="alert">{t(errorKey)}</p>}
      {validation.ok && status === 'ready' && (
        <div className="model-viewer-controls" role="group">
          <button onClick={() => runtimeRef.current?.resetView()} type="button">{t('viewer.resetView')}</button>
          <button aria-pressed={wireframe} onClick={() => setWireframe((current) => !current)} type="button">{t('viewer.wireframe')}</button>
          <button aria-pressed={showGrid} onClick={() => setShowGrid((current) => !current)} type="button">{t('viewer.grid')}</button>
          <button aria-pressed={showAxes} onClick={() => setShowAxes((current) => !current)} type="button">{t('viewer.axes')}</button>
        </div>
      )}
    </section>
  )
}
