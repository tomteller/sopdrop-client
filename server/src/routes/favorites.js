/**
 * Favorites routes
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { authenticate, optionalAuth } from '../middleware/auth.js';
import { NotFoundError } from '../middleware/errorHandler.js';

const router = Router();

/**
 * GET /favorites
 * Get current user's favorites
 */
router.get('/', authenticate, async (req, res, next) => {
  try {
    const result = await query(`
      SELECT
        a.asset_id as id,
        a.name,
        a.slug,
        u.username as owner,
        a.asset_type as type,
        a.houdini_context as context,
        a.description,
        a.tags,
        a.latest_version,
        a.download_count,
        a.favorite_count,
        v.thumbnail_url,
        f.created_at as favorited_at
      FROM favorites f
      JOIN assets a ON f.asset_id = a.id
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE f.user_id = $1 AND a.is_public = true
      ORDER BY f.created_at DESC
    `, [req.user.id]);

    res.json({
      favorites: result.rows.map(a => ({
        id: a.id,
        name: a.name,
        slug: `${a.owner}/${a.slug}`,
        owner: a.owner,
        type: a.type,
        context: a.context,
        description: a.description,
        tags: a.tags || [],
        latestVersion: a.latest_version,
        downloadCount: a.download_count,
        favoriteCount: a.favorite_count,
        thumbnailUrl: a.thumbnail_url,
        favoritedAt: a.favorited_at,
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /favorites/:slug
 * Add asset to favorites
 */
router.post('/:slug(*)', authenticate, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new NotFoundError('Asset not found');
    }

    const [owner, assetSlug] = parts;

    // Get asset
    const assetResult = await query(`
      SELECT a.id FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2 AND a.is_public = true
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const assetId = assetResult.rows[0].id;

    // Add to favorites (ignore if already exists)
    await query(`
      INSERT INTO favorites (user_id, asset_id)
      VALUES ($1, $2)
      ON CONFLICT (user_id, asset_id) DO NOTHING
    `, [req.user.id, assetId]);

    // Update favorite count
    await query(`
      UPDATE assets SET favorite_count = (
        SELECT COUNT(*) FROM favorites WHERE asset_id = $1
      ) WHERE id = $1
    `, [assetId]);

    res.json({ success: true, favorited: true });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /favorites/:slug
 * Remove asset from favorites
 */
router.delete('/:slug(*)', authenticate, async (req, res, next) => {
  try {
    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      throw new NotFoundError('Asset not found');
    }

    const [owner, assetSlug] = parts;

    // Get asset
    const assetResult = await query(`
      SELECT a.id FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2
    `, [owner.toLowerCase(), assetSlug.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const assetId = assetResult.rows[0].id;

    // Remove from favorites
    await query(`
      DELETE FROM favorites WHERE user_id = $1 AND asset_id = $2
    `, [req.user.id, assetId]);

    // Update favorite count
    await query(`
      UPDATE assets SET favorite_count = (
        SELECT COUNT(*) FROM favorites WHERE asset_id = $1
      ) WHERE id = $1
    `, [assetId]);

    res.json({ success: true, favorited: false });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /favorites/check/:slug
 * Check if asset is favorited by current user
 */
router.get('/check/:slug(*)', optionalAuth, async (req, res, next) => {
  try {
    if (!req.user) {
      return res.json({ favorited: false });
    }

    const slug = decodeURIComponent(req.params.slug);
    const parts = slug.split('/');

    if (parts.length !== 2) {
      return res.json({ favorited: false });
    }

    const [owner, assetSlug] = parts;

    const result = await query(`
      SELECT f.id FROM favorites f
      JOIN assets a ON f.asset_id = a.id
      JOIN users u ON a.owner_id = u.id
      WHERE f.user_id = $1 AND u.username = $2 AND a.slug = $3
    `, [req.user.id, owner.toLowerCase(), assetSlug.toLowerCase()]);

    res.json({ favorited: result.rows.length > 0 });
  } catch (error) {
    next(error);
  }
});

export default router;
