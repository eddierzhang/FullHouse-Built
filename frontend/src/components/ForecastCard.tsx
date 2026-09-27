import { useEffect, useState } from 'react'
import { type Forecast, fetchForecast } from '../api/client'
import { money } from '../lib/format'
import { ForecastChart } from './Charts'
import { Icon } from './Icon'

const CONFIDENCE_BADGE: Record<Forecast['confidence'], string> = {
  high: 'badge-success',
  medium: 'badge-info',
  low: 'badge-warning',
  none: '',
}

/**
 * The next week's projected revenue, computed from real completed trading
 * days -- replacing a panel that showed a fixed, invented figure. Says what
 * it is built on, because a projection from five days of trading deserves
 * less trust than one from five weeks.
 */
export function ForecastCard() {
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    fetchForecast(7)
      .then(setForecast)
      .catch(() => setFailed(true))
  }, [])

  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2 className="card-title">Next 7 days</h2>
          <div className="card-subtitle">Projected revenue per day</div>
        </div>
        {forecast && forecast.confidence !== 'none' && (
          <span className={`badge ${CONFIDENCE_BADGE[forecast.confidence]}`}>{forecast.confidence} confidence</span>
        )}
      </div>

      <div className="card-body">
        {failed && <div className="muted">Couldn’t load the forecast.</div>}
        {!forecast && !failed && <div className="skeleton" style={{ height: 180 }} />}

        {forecast && forecast.method === 'none' && (
          <div className="empty" style={{ padding: '24px 0' }}>
            <div className="empty-title">No forecast yet</div>
            <div className="small">{forecast.message}</div>
          </div>
        )}

        {forecast && forecast.method !== 'none' && (
          <>
            <div className="row" style={{ gap: 28, marginBottom: 12 }}>
              <div>
                <div className="stat-label">Revenue</div>
                <div className="stat-value" style={{ fontSize: 22 }}>
                  {money(forecast.total_revenue)}
                </div>
              </div>
              <div>
                <div className="stat-label">Gross profit</div>
                <div className="stat-value" style={{ fontSize: 22 }}>
                  {money(forecast.total_profit)}
                </div>
              </div>
            </div>
            <ForecastChart days={forecast.days} />
            <div className="row muted small" style={{ marginTop: 10 }}>
              <Icon name="info" size={14} />
              {forecast.message} Whiskers show the likely range.
            </div>
          </>
        )}
      </div>
    </section>
  )
}
