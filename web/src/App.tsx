import { useState, useEffect } from 'react';

interface HealthStatus {
  status: string;
  version: string;
  db: string;
  redis: string;
}

function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const response = await fetch('/healthz');
        const data = await response.json();
        setHealth(data);
      } catch (error) {
        console.error('Health check failed:', error);
      } finally {
        setLoading(false);
      }
    };

    checkHealth();
  }, []);

  return (
    <div style={{ padding: '2rem', fontFamily: 'system-ui, sans-serif' }}>
      <h1>Expense Tracker</h1>
      
      {loading ? (
        <p>Loading...</p>
      ) : health ? (
        <div>
          <p>Status: {health.status}</p>
          <p>Version: {health.version}</p>
          <p>Database: {health.db}</p>
          <p>Redis: {health.redis}</p>
        </div>
      ) : (
        <p>Unable to connect to API</p>
      )}
    </div>
  );
}

export default App;