/**
 * Saved Assets Routes
 *
 * User's library of copied/downloaded/purchased assets.
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { authenticate } from '../middleware/auth.js';
import { NotFoundError, ValidationError } from '../middleware/errorHandler.js';

const router = Router();

/**
 * GET /saved
 * List user's saved assets
 *
 * Query params:
 *   sort - 'recent' (default), 'name', 'downloads'
 *   source - Filter by source ('copy', 'download', 'purchase', 'manual')
 *   folder - Filter by folder name
 *   limit - Max results (default 50, max 100)
 *   offset - Pagination offset
 */
router.get('/', authenticate, async (req, res, next) => {
  try {
    const {
      sort = 'recent',
      source,
      folder,
      limit = 50,
      offset = 0,
    } = req.query;

    let whereClause = 'WHERE sa.user_id = $1 AND a.is_deprecated = false';
    const params = [req.user.id];
    let paramIndex = 2;

    if (source) {
      whereClause += ` AND sa.source = $${paramIndex}`;
      params.push(source);
      paramIndex++;
    }

    if (folder) {
      whereClause += ` AND sa.folder = $${paramIndex}`;
      params.push(folder);
      paramIndex++;
    }

    // Sort order
    let orderBy;
    switch (sort) {
      case 'name':
        orderBy = 'ORDER BY a.name ASC';
        break;
      case 'downloads':
        orderBy = 'ORDER BY a.download_count DESC';
        break;
      default:
        orderBy = 'ORDER BY sa.saved_at DESC';
    }

    const limitVal = Math.min(parseInt(limit) || 50, 100);
    const offsetVal = parseInt(offset) || 0;

    // Get saved assets with full asset details
    const result = await query(`
      SELECT
        sa.id as saved_id,
        sa.saved_at,
        sa.source,
        sa.notes,
        sa.folder,
        a.id,
        a.asset_id,
        a.name,
        a.slug,
        a.description,
        a.asset_type as type,
        a.houdini_context as context,
        a.tags,
        a.download_count,
        a.is_deprecated,
        u.username as owner_username,
        u.avatar_url as owner_avatar,
        v.version as latest_version,
        v.thumbnail_url,
        sv.version as saved_version
      FROM saved_assets sa
      JOIN assets a ON sa.asset_id = a.id
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      LEFT JOIN versions sv ON sa.version_id = sv.id
      ${whereClause}
      ${orderBy}
      LIMIT $${paramIndex} OFFSET $${paramIndex + 1}
    `, [...params, limitVal, offsetVal]);

    // Get total count
    const countResult = await query(`
      SELECT COUNT(*) as total
      FROM saved_assets sa
      JOIN assets a ON sa.asset_id = a.id
      ${whereClause}
    `, params);

    // Get folder list for this user
    const foldersResult = await query(`
      SELECT DISTINCT folder, COUNT(*) as count
      FROM saved_assets
      WHERE user_id = $1 AND folder IS NOT NULL
      GROUP BY folder
      ORDER BY folder ASC
    `, [req.user.id]);

    res.json({
      assets: result.rows.map(row => ({
        savedId: row.saved_id,
        savedAt: row.saved_at,
        source: row.source,
        notes: row.notes,
        folder: row.folder,
        savedVersion: row.saved_version,
        id: row.id,
        assetId: row.asset_id,
        name: row.name,
        slug: `${row.owner_username}/${row.slug}`,
        description: row.description,
        type: row.type,
        context: row.context,
        tags: row.tags || [],
        downloadCount: row.download_count,
        isDeprecated: row.is_deprecated,
        latestVersion: row.latest_version,
        thumbnailUrl: row.thumbnail_url,
        owner: {
          username: row.owner_username,
          avatarUrl: row.owner_avatar,
        },
      })),
      total: parseInt(countResult.rows[0].total),
      folders: foldersResult.rows.map(f => ({
        name: f.folder,
        count: parseInt(f.count),
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /saved/:assetId
 * Save an asset to user's library
 *
 * Body:
 *   source - 'copy', 'download', 'purchase', 'manual' (optional, default 'manual')
 *   versionId - Which version was saved (optional)
 *   notes - User notes (optional)
 *   folder - Folder name (optional)
 */
router.post('/:assetId', authenticate, async (req, res, next) => {
  try {
    const { assetId } = req.params;
    const { source = 'manual', versionId, notes, folder } = req.body;

    // Validate source
    const validSources = ['copy', 'download', 'purchase', 'manual'];
    if (!validSources.includes(source)) {
      throw new ValidationError('Invalid source');
    }

    // Check asset exists (handle both numeric ID and UUID)
    const isNumeric = /^\d+$/.test(assetId);
    const assetResult = await query(
      isNumeric
        ? 'SELECT id, name FROM assets WHERE id = $1'
        : 'SELECT id, name FROM assets WHERE asset_id = $1',
      [isNumeric ? parseInt(assetId) : assetId]
    );

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = assetResult.rows[0];

    // Insert or update (upsert)
    const result = await query(`
      INSERT INTO saved_assets (user_id, asset_id, version_id, source, notes, folder)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT (user_id, asset_id) DO UPDATE SET
        version_id = COALESCE(EXCLUDED.version_id, saved_assets.version_id),
        source = EXCLUDED.source,
        notes = COALESCE(EXCLUDED.notes, saved_assets.notes),
        folder = COALESCE(EXCLUDED.folder, saved_assets.folder),
        saved_at = NOW()
      RETURNING id, saved_at
    `, [req.user.id, asset.id, versionId || null, source, notes || null, folder || null]);

    res.json({
      success: true,
      savedId: result.rows[0].id,
      savedAt: result.rows[0].saved_at,
      assetName: asset.name,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /saved/batch
 * Remove multiple assets from user's library
 *
 * Body:
 *   assetIds - Array of numeric asset IDs to remove
 */
router.delete('/batch', authenticate, async (req, res, next) => {
  try {
    const { assetIds } = req.body;

    if (!Array.isArray(assetIds) || assetIds.length === 0) {
      throw new ValidationError('assetIds must be a non-empty array');
    }

    if (assetIds.length > 100) {
      throw new ValidationError('Cannot remove more than 100 assets at once');
    }

    // Validate all IDs are integers
    const ids = assetIds.map(id => {
      const parsed = parseInt(id);
      if (isNaN(parsed)) throw new ValidationError('All assetIds must be integers');
      return parsed;
    });

    const placeholders = ids.map((_, i) => `$${i + 2}`).join(', ');
    const result = await query(
      `DELETE FROM saved_assets WHERE user_id = $1 AND asset_id IN (${placeholders}) RETURNING id`,
      [req.user.id, ...ids]
    );

    res.json({ success: true, removed: result.rows.length });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /saved/:assetId
 * Remove an asset from user's library
 */
router.delete('/:assetId', authenticate, async (req, res, next) => {
  try {
    const { assetId } = req.params;

    // Get asset ID (handle both numeric and UUID)
    const isNumeric = /^\d+$/.test(assetId);
    const assetResult = await query(
      isNumeric
        ? 'SELECT id FROM assets WHERE id = $1'
        : 'SELECT id FROM assets WHERE asset_id = $1',
      [isNumeric ? parseInt(assetId) : assetId]
    );

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const result = await query(
      'DELETE FROM saved_assets WHERE user_id = $1 AND asset_id = $2 RETURNING id',
      [req.user.id, assetResult.rows[0].id]
    );

    if (result.rows.length === 0) {
      throw new NotFoundError('Asset not in your library');
    }

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /saved/:assetId
 * Update saved asset (notes, folder)
 */
router.put('/:assetId', authenticate, async (req, res, next) => {
  try {
    const { assetId } = req.params;
    const { notes, folder } = req.body;

    // Get asset ID (handle both numeric and UUID)
    const isNumeric = /^\d+$/.test(assetId);
    const assetResult = await query(
      isNumeric
        ? 'SELECT id FROM assets WHERE id = $1'
        : 'SELECT id FROM assets WHERE asset_id = $1',
      [isNumeric ? parseInt(assetId) : assetId]
    );

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const result = await query(`
      UPDATE saved_assets
      SET notes = $3, folder = $4
      WHERE user_id = $1 AND asset_id = $2
      RETURNING id, notes, folder
    `, [req.user.id, assetResult.rows[0].id, notes || null, folder || null]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Asset not in your library');
    }

    res.json({
      success: true,
      notes: result.rows[0].notes,
      folder: result.rows[0].folder,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /saved/check/:assetId
 * Check if an asset is saved
 */
router.get('/check/:assetId', authenticate, async (req, res, next) => {
  try {
    const { assetId } = req.params;
    const isNumeric = /^\d+$/.test(assetId);

    const result = await query(`
      SELECT sa.id, sa.saved_at, sa.source
      FROM saved_assets sa
      JOIN assets a ON sa.asset_id = a.id
      WHERE sa.user_id = $1 AND ${isNumeric ? 'a.id = $2' : 'a.asset_id = $2'}
    `, [req.user.id, isNumeric ? parseInt(assetId) : assetId]);

    res.json({
      saved: result.rows.length > 0,
      savedAt: result.rows[0]?.saved_at || null,
      source: result.rows[0]?.source || null,
    });
  } catch (error) {
    next(error);
  }
});

export default router;
