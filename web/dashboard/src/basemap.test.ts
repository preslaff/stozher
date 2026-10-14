import { describe, expect, it } from 'vitest'
import { baseStyle } from './basemap'

const ASSETS = 'http://kit.local:5173/basemap'
const FILE = 'http://kit.local:5173/basemap/bulgaria.pmtiles'

describe('baseStyle', () => {
  it('uses only the local file, fonts and sprites: nothing is fetched from the internet', () => {
    const style = baseStyle({ pmtilesUrl: FILE, assetBase: ASSETS })
    const urls = JSON.stringify(style).match(/https?:\/\/[^"/]+/g) ?? []
    expect(urls.length).toBeGreaterThan(0)
    expect(new Set(urls)).toEqual(new Set(['http://kit.local:5173']))
    expect(style.glyphs).toBe(`${ASSETS}/fonts/{fontstack}/{range}.pbf`)
    expect(style.sprite).toBe(`${ASSETS}/sprites/v4/dark`)
    expect(style.sources.protomaps).toMatchObject({ type: 'vector', url: `pmtiles://${FILE}` })
    expect(style.layers.length).toBeGreaterThan(20)
  })

  it('uses a raster URL only when one is set explicitly', () => {
    const style = baseStyle({
      pmtilesUrl: FILE,
      assetBase: ASSETS,
      rasterUrl: 'https://tiles.example/{z}/{x}/{y}.png',
    })
    expect(style.sources.base).toMatchObject({ type: 'raster' })
    expect(style.sources.protomaps).toBeUndefined()
  })

  it('without a file or a raster URL there is only the background', () => {
    const style = baseStyle({ pmtilesUrl: '', assetBase: ASSETS })
    expect(style.layers.map((l) => l.id)).toEqual(['bg'])
    expect(JSON.stringify(style)).not.toContain('http')
  })
})
