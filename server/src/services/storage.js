/**
 * Storage service - Abstracts file storage for R2 (production) and local filesystem (development)
 *
 * When R2_BUCKET_NAME is set, uses Cloudflare R2 (S3-compatible).
 * Otherwise, falls back to local filesystem for development.
 *
 * Storage keys are relative paths like "nodes/uuid.sopdrop" or "hdas/uuid.hda".
 * The /library/ prefix in the database is a legacy convention maintained for compatibility.
 */

import fs from 'fs';
import path from 'path';

const LIBRARY_BASE = process.env.LIBRARY_PATH || path.join(process.cwd(), 'library');

const isR2Configured = () => !!process.env.R2_BUCKET_NAME;

// Lazy-loaded S3 client (only created when first needed)
let _s3Client = null;

async function getS3Client() {
  if (_s3Client) return _s3Client;

  const { S3Client } = await import('@aws-sdk/client-s3');

  _s3Client = new S3Client({
    region: 'auto',
    endpoint: `https://${process.env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
    credentials: {
      accessKeyId: process.env.R2_ACCESS_KEY_ID,
      secretAccessKey: process.env.R2_SECRET_ACCESS_KEY,
    },
  });

  return _s3Client;
}

function getBucket() {
  return process.env.R2_BUCKET_NAME;
}

// ─── Local filesystem helpers ───────────────────────────────────────────────

function localKeyToPath(key) {
  return path.join(LIBRARY_BASE, key);
}

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

// ─── Public API ─────────────────────────────────────────────────────────────

/**
 * Upload a file to storage.
 * @param {string} key - Storage key (e.g. "nodes/uuid.sopdrop", "hdas/uuid.hda")
 * @param {Buffer|string} data - File content
 * @param {string} [contentType] - MIME type
 */
export async function upload(key, data, contentType) {
  const buf = Buffer.isBuffer(data) ? data : Buffer.from(data, 'utf-8');

  if (isR2Configured()) {
    const { PutObjectCommand } = await import('@aws-sdk/client-s3');
    const client = await getS3Client();
    await client.send(new PutObjectCommand({
      Bucket: getBucket(),
      Key: key,
      Body: buf,
      ContentType: contentType,
    }));
  } else {
    const filePath = localKeyToPath(key);
    ensureDir(filePath);
    fs.writeFileSync(filePath, buf);
  }
}

/**
 * Download a file from storage as a Buffer.
 * @param {string} key
 * @returns {Promise<Buffer>}
 */
export async function download(key) {
  if (isR2Configured()) {
    const { GetObjectCommand } = await import('@aws-sdk/client-s3');
    const client = await getS3Client();
    const resp = await client.send(new GetObjectCommand({
      Bucket: getBucket(),
      Key: key,
    }));
    const chunks = [];
    for await (const chunk of resp.Body) {
      chunks.push(chunk);
    }
    return Buffer.concat(chunks);
  } else {
    return fs.readFileSync(localKeyToPath(key));
  }
}

/**
 * Get a readable stream for a file.
 * @param {string} key
 * @returns {Promise<import('stream').Readable>}
 */
export async function stream(key) {
  if (isR2Configured()) {
    const { GetObjectCommand } = await import('@aws-sdk/client-s3');
    const client = await getS3Client();
    const resp = await client.send(new GetObjectCommand({
      Bucket: getBucket(),
      Key: key,
    }));
    return resp.Body;
  } else {
    return fs.createReadStream(localKeyToPath(key));
  }
}

/**
 * Check if a file exists in storage.
 * @param {string} key
 * @returns {Promise<boolean>}
 */
export async function exists(key) {
  if (isR2Configured()) {
    const { HeadObjectCommand } = await import('@aws-sdk/client-s3');
    const client = await getS3Client();
    try {
      await client.send(new HeadObjectCommand({
        Bucket: getBucket(),
        Key: key,
      }));
      return true;
    } catch {
      return false;
    }
  } else {
    return fs.existsSync(localKeyToPath(key));
  }
}

/**
 * Delete a file from storage. Silently succeeds if file doesn't exist.
 * @param {string} key
 */
export async function remove(key) {
  if (!key) return;

  if (isR2Configured()) {
    const { DeleteObjectCommand } = await import('@aws-sdk/client-s3');
    const client = await getS3Client();
    try {
      await client.send(new DeleteObjectCommand({
        Bucket: getBucket(),
        Key: key,
      }));
    } catch {
      // Ignore errors on delete
    }
  } else {
    const filePath = localKeyToPath(key);
    try {
      if (fs.existsSync(filePath)) {
        fs.unlinkSync(filePath);
      }
    } catch {
      // Ignore errors on delete
    }
  }
}

/**
 * Get the public URL for a stored file.
 * In R2 mode, returns the configured public URL.
 * In local mode, returns a relative /library/... path.
 * @param {string} key
 * @returns {string}
 */
export function getPublicUrl(key) {
  if (isR2Configured() && process.env.R2_PUBLIC_URL) {
    return `${process.env.R2_PUBLIC_URL.replace(/\/$/, '')}/${key}`;
  }
  return `/library/${key}`;
}

/**
 * Convert a legacy /library/... DB path to a storage key.
 * e.g. "/library/nodes/uuid.sopdrop" → "nodes/uuid.sopdrop"
 * @param {string} legacyPath
 * @returns {string|null}
 */
export function pathToKey(legacyPath) {
  if (!legacyPath) return null;
  const key = legacyPath.replace(/^\/library\//, '');

  // Prevent path traversal
  if (key.includes('..') || key.startsWith('/') || key.includes('\0')) {
    return null;
  }

  return key;
}

/**
 * Convert a storage key to a legacy /library/... path for DB storage.
 * e.g. "nodes/uuid.sopdrop" → "/library/nodes/uuid.sopdrop"
 * @param {string} key
 * @returns {string}
 */
export function keyToPath(key) {
  return `/library/${key}`;
}

/**
 * Convert a DB path (/library/...) to a browser-accessible public URL.
 * In R2 mode: returns full R2 public URL.
 * In local mode: returns the /library/... path as-is (served by express.static).
 * Handles null/undefined gracefully.
 * @param {string|null} dbPath
 * @returns {string|null}
 */
export function toPublicUrl(dbPath) {
  if (!dbPath) return null;
  const key = pathToKey(dbPath);
  if (!key) return null;
  return getPublicUrl(key);
}

export default {
  upload,
  download,
  stream,
  exists,
  remove,
  getPublicUrl,
  pathToKey,
  keyToPath,
  toPublicUrl,
  isR2Configured,
};
