/**
 * Database connection and initialization
 */

import pkg from 'pg';
const { Pool } = pkg;
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// PostgreSQL connection pool
// Prefer DATABASE_URL (standard for Neon, Heroku, etc.), fall back to individual vars
//
// SSL is opt-in via DB_SSL=true. Cloud/managed Postgres (Neon, Heroku, RDS)
// requires it; the bundled on-prem Postgres image has SSL disabled by default,
// so leaving it off here lets a fresh `docker compose up` succeed without
// any extra config. DB_SSL_REJECT_UNAUTHORIZED only controls cert verification
// once SSL is already enabled.
const sslConfig = process.env.DB_SSL === 'true'
  ? { rejectUnauthorized: process.env.DB_SSL_REJECT_UNAUTHORIZED !== 'false' }
  : false;

const poolConfig = process.env.DATABASE_URL
  ? {
      connectionString: process.env.DATABASE_URL,
      ssl: sslConfig,
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    }
  : {
      user: process.env.DB_USER || 'postgres',
      host: process.env.DB_HOST || 'localhost',
      database: process.env.DB_NAME || 'sopdrop',
      password: process.env.DB_PASSWORD,
      port: parseInt(process.env.DB_PORT) || 5432,
      ssl: sslConfig,
      max: 10,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    };

const pool = new Pool(poolConfig);

/**
 * Test database connection
 */
export async function testConnection() {
  const client = await pool.connect();
  try {
    await client.query('SELECT 1');
    return true;
  } finally {
    client.release();
  }
}

/**
 * Initialize database schema
 */
export async function initDB() {
  const client = await pool.connect();

  try {
    // Read and execute schema
    const schemaPath = path.join(__dirname, 'schema.sql');
    const schema = fs.readFileSync(schemaPath, 'utf-8');

    await client.query('BEGIN');
    await client.query(schema);
    await client.query('COMMIT');

    console.log('✅ Database schema initialized');
  } catch (error) {
    await client.query('ROLLBACK');
    console.error('❌ Database initialization failed:', error.message);
    throw error;
  } finally {
    client.release();
  }
}

/**
 * Query helper with automatic connection management
 */
export async function query(text, params) {
  const start = Date.now();
  const result = await pool.query(text, params);
  const duration = Date.now() - start;

  // SQL debug logging (development only, never in production)
  if (process.env.DEBUG_SQL === 'true' && process.env.NODE_ENV !== 'production') {
    console.log('SQL:', { text, duration, rows: result.rowCount });
  }

  return result;
}

/**
 * Get a client for transactions
 */
export async function getClient() {
  return pool.connect();
}

export { pool };
export default { pool, query, getClient, testConnection, initDB };
