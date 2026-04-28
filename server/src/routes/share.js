/**
 * Temporary Share Routes
 *
 * Quick sharing of node snippets via short codes.
 * Shares expire after 24 hours and require no publish workflow.
 *
 * Flow:
 * 1. Houdini user selects nodes, clicks Share
 * 2. Client exports package, POSTs to /share
 * 3. Server returns share code (e.g., TC-4B9X)
 * 4. User sends code/link to colleague
 * 5. Colleague pastes in Houdini — client GETs /share/:code/download
 */

import { Router } from 'express';
import crypto from 'crypto';
import { v4 as uuidv4 } from 'uuid';
import { query } from '../models/db.js';
import { authenticate, requireScope } from '../middleware/auth.js';
import { ValidationError, NotFoundError } from '../middleware/errorHandler.js';
import { validateChopPackage, extractAssetMetadata } from '../services/validation.js';
import { shareLimiter, downloadLimiter } from '../middleware/security.js';
import storage from '../services/storage.js';

const router = Router();

const SHARE_EXPIRATION_MS = 24 * 60 * 60 * 1000; // 24 hours

/**
 * Generate a short share code like "TC-4B9X"
 */
function generateShareCode() {
  const letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ'; // no I, O to avoid confusion
  const alphanumeric = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no 0, 1, I, O

  const prefix = letters[crypto.randomInt(letters.length)]
    + letters[crypto.randomInt(letters.length)];

  let suffix = '';
  for (let i = 0; i < 4; i++) {
    suffix += alphanumeric[crypto.randomInt(alphanumeric.length)];
  }

  return `${prefix}-${suffix}`;
}

/**
 * POST /share
 * Create a temporary share from a .sopdrop package
 */
router.post('/', authenticate, requireScope('write'), shareLimiter, async (req, res, next) => {
  try {
    const { package: chopPackage, name } = req.body;

    if (!chopPackage) {
      throw new ValidationError('Package data is required');
    }

    // Parse package if it's a string
    let packageData;
    try {
      packageData = typeof chopPackage === 'string' ? JSON.parse(chopPackage) : chopPackage;
    } catch (e) {
      throw new ValidationError('Invalid package JSON');
    }

    // Validate the package
    validateChopPackage(packageData);

    const assetMeta = extractAssetMetadata(packageData);

    // Generate unique share code (retry on collision)
    let shareCode;
    let attempts = 0;
    while (attempts < 5) {
      shareCode = generateShareCode();
      const existing = await query(
        'SELECT id FROM temp_shares WHERE share_code = $1',
        [shareCode]
      );
      if (existing.rows.length === 0) break;
      attempts++;
    }
    if (attempts >= 5) {
      throw new Error('Failed to generate unique share code');
    }

    // Save package to temp storage
    const fileUuid = uuidv4();
    const content = JSON.stringify(packageData, null, 2);
    const key = `temp/${fileUuid}.sopdrop`;

    await storage.upload(key, content, 'application/json');

    const filePath = storage.keyToPath(key);
    const fileHash = crypto.createHash('sha256').update(content).digest('hex');

    // Insert into database
    const expiresAt = new Date(Date.now() + SHARE_EXPIRATION_MS);

    await query(`
      INSERT INTO temp_shares (
        share_code, file_path, file_hash,
        name, houdini_context, node_count, node_names,
        created_by, expires_at
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    `, [
      shareCode,
      filePath,
      fileHash,
      name || null,
      assetMeta.context,
      assetMeta.nodeCount,
      assetMeta.nodeNames,
      req.user.id,
      expiresAt,
    ]);

    const webUrl = process.env.WEB_URL || 'http://localhost:5173';

    res.status(201).json({
      shareCode,
      shareUrl: `${webUrl}/s/${shareCode}`,
      expiresAt: expiresAt.toISOString(),
      metadata: {
        context: assetMeta.context,
        nodeCount: assetMeta.nodeCount,
        nodeNames: assetMeta.nodeNames,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /share/:code
 * Get share metadata (public, no auth)
 */
router.get('/:code', downloadLimiter, async (req, res, next) => {
  try {
    const { code } = req.params;

    const result = await query(`
      SELECT ts.*, u.username
      FROM temp_shares ts
      LEFT JOIN users u ON ts.created_by = u.id
      WHERE ts.share_code = $1 AND ts.expires_at > NOW()
    `, [code.toUpperCase()]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Share not found or expired');
    }

    const share = result.rows[0];

    res.json({
      shareCode: share.share_code,
      name: share.name,
      context: share.houdini_context,
      nodeCount: share.node_count,
      nodeNames: share.node_names,
      sharedBy: share.username || null,
      expiresAt: share.expires_at,
      createdAt: share.created_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /share/:code/download
 * Download the shared .sopdrop package (public, no auth)
 */
router.get('/:code/download', downloadLimiter, async (req, res, next) => {
  try {
    const { code } = req.params;

    const result = await query(`
      SELECT * FROM temp_shares
      WHERE share_code = $1 AND expires_at > NOW()
    `, [code.toUpperCase()]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Share not found or expired');
    }

    const share = result.rows[0];
    const key = storage.pathToKey(share.file_path);

    if (!key || !(await storage.exists(key))) {
      throw new NotFoundError('Share file not found');
    }

    const buf = await storage.download(key);
    const packageData = JSON.parse(buf.toString('utf-8'));

    res.json(packageData);
  } catch (error) {
    next(error);
  }
});

/**
 * Cleanup expired temp shares (should be called periodically)
 */
export async function cleanupExpiredShares() {
  try {
    // Get expired shares
    const result = await query(`
      SELECT id, file_path FROM temp_shares
      WHERE expires_at < NOW()
    `);

    // Delete files from storage
    for (const share of result.rows) {
      if (share.file_path) {
        const key = storage.pathToKey(share.file_path);
        if (key) {
          try {
            await storage.remove(key);
          } catch (err) {
            console.error(`Failed to remove share file ${key}:`, err.message);
          }
        }
      }
    }

    // Delete from database
    const deleted = await query(`
      DELETE FROM temp_shares WHERE expires_at < NOW()
    `);

    if (deleted.rowCount > 0) {
      console.log(`Cleaned up ${deleted.rowCount} expired shares`);
    }
  } catch (error) {
    console.error('Error cleaning up shares:', error.message);
  }
}

export default router;
