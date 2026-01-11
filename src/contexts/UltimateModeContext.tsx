'use client'

import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

interface UltimateModeContextType {
  ultimateMode: boolean
  setUltimateMode: (mode: boolean) => void
  includeDetails: boolean
  setIncludeDetails: (details: boolean) => void
}

const UltimateModeContext = createContext<UltimateModeContextType | undefined>(undefined)

export function UltimateModeProvider({ children }: { children: ReactNode }) {
  const [ultimateMode, setUltimateModeState] = useState(false)
  const [includeDetails, setIncludeDetailsState] = useState(false)

  // localStorageから設定を読み込み
  useEffect(() => {
    const savedMode = localStorage.getItem('ultimateMode')
    const savedDetails = localStorage.getItem('includeDetails')
    
    if (savedMode !== null) {
      setUltimateModeState(savedMode === 'true')
    }
    if (savedDetails !== null) {
      setIncludeDetailsState(savedDetails === 'true')
    }
  }, [])

  // Ultimate版モードを切り替え
  const setUltimateMode = (mode: boolean) => {
    setUltimateModeState(mode)
    localStorage.setItem('ultimateMode', mode.toString())
    
    // Ultimate版をOFFにした場合、includeDetailsもOFFにする
    if (!mode && includeDetails) {
      setIncludeDetails(false)
    }
  }

  // 詳細取得オプションを切り替え
  const setIncludeDetails = (details: boolean) => {
    setIncludeDetailsState(details)
    localStorage.setItem('includeDetails', details.toString())
  }

  return (
    <UltimateModeContext.Provider value={{ ultimateMode, setUltimateMode, includeDetails, setIncludeDetails }}>
      {children}
    </UltimateModeContext.Provider>
  )
}

export function useUltimateMode() {
  const context = useContext(UltimateModeContext)
  if (context === undefined) {
    throw new Error('useUltimateMode must be used within UltimateModeProvider')
  }
  return context
}
