/**
 * Curated Collections routes
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { authenticate, optionalAuth } from '../middleware/auth.js';
import { NotFoundError, ForbiddenError, ValidationError } from '../middleware/errorHandler.js';

const router = Router();

/**
 * GET /collections
 * List all public curated collections
 */
router.get('/', async (req, res, next) => {
  try {
    const { featured, staff_picks, page = 1, limit = 20 } = req.query;
    const offset = (parseInt(page) - 1) * parseInt(limit);

    let whereClause = 'WHERE cc.is_public = true';
    const params = [];

    if (featured === 'true') {
      whereClause += ' AND cc.is_featured = true';
    }
    if (staff_picks === 'true') {
      whereClause += ' AND cc.is_staff_pick = true';
    }

    const result = await query(`
      SELECT
        cc.collection_id as id,
        cc.name,
        cc.slug,
        cc.description,
        cc.cover_image,
        cc.is_featured,
        cc.is_staff_pick,
        cc.asset_count,
        cc.view_count,
        cc.created_at,
        u.username as curator,
        u.avatar_url as curator_avatar
      FROM curated_collections cc
      LEFT JOIN users u ON cc.curator_id = u.id
      ${whereClause}
      ORDER BY cc.position ASC, cc.created_at DESC
      LIMIT $${params.length + 1} OFFSET $${params.length + 2}
    `, [...params, parseInt(limit), offset]);

    const countResult = await query(`
      SELECT COUNT(*) as total FROM curated_collections cc ${whereClause}
    `, params);

    res.json({
      collections: result.rows.map(c => ({
        id: c.id,
        name: c.name,
        slug: c.slug,
        description: c.description,
        coverImage: c.cover_image,
        isFeatured: c.is_featured,
        isStaffPick: c.is_staff_pick,
        assetCount: c.asset_count,
        viewCount: c.view_count,
        createdAt: c.created_at,
        curator: c.curator,
        curatorAvatar: c.curator_avatar,
      })),
      pagination: {
        page: parseInt(page),
        limit: parseInt(limit),
        total: parseInt(countResult.rows[0].total),
        totalPages: Math.ceil(parseInt(countResult.rows[0].total) / parseInt(limit)),
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /collections/featured
 * Get featured collections for homepage
 */
router.get('/featured', async (req, res, next) => {
  try {
    const result = await query(`
      SELECT
        cc.collection_id as id,
        cc.name,
        cc.slug,
        cc.description,
        cc.cover_image,
        cc.is_staff_pick,
        cc.asset_count,
        u.username as curator,
        u.avatar_url as curator_avatar,
        (
          SELECT json_agg(json_build_object(
            'thumbnail', v.thumbnail_url,
            'name', a.name
          ) ORDER BY cca.position ASC)
          FROM curated_collection_assets cca
          JOIN assets a ON cca.asset_id = a.id
          LEFT JOIN versions v ON a.latest_version_id = v.id
          WHERE cca.collection_id = cc.id
          LIMIT 4
        ) as preview_assets
      FROM curated_collections cc
      LEFT JOIN users u ON cc.curator_id = u.id
      WHERE cc.is_featured = true AND cc.is_public = true
      ORDER BY cc.position ASC
      LIMIT 6
    `);

    res.json({
      collections: result.rows.map(c => ({
        id: c.id,
        name: c.name,
        slug: c.slug,
        description: c.description,
        coverImage: c.cover_image,
        isStaffPick: c.is_staff_pick,
        assetCount: c.asset_count,
        curator: c.curator,
        curatorAvatar: c.curator_avatar,
        previewAssets: c.preview_assets || [],
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /collections/:slug
 * Get a single collection with its assets
 */
router.get('/:slug', optionalAuth, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { page = 1, limit = 20 } = req.query;
    const offset = (parseInt(page) - 1) * parseInt(limit);

    // Get collection
    const colResult = await query(`
      SELECT
        cc.id,
        cc.collection_id,
        cc.name,
        cc.slug,
        cc.description,
        cc.cover_image,
        cc.is_featured,
        cc.is_staff_pick,
        cc.is_public,
        cc.asset_count,
        cc.view_count,
        cc.created_at,
        cc.curator_id,
        u.username as curator,
        u.avatar_url as curator_avatar,
        u.display_name as curator_display_name
      FROM curated_collections cc
      LEFT JOIN users u ON cc.curator_id = u.id
      WHERE cc.slug = $1
    `, [slug]);

    if (colResult.rows.length === 0) {
      throw new NotFoundError('Collection not found');
    }

    const collection = colResult.rows[0];

    // Check visibility
    if (!collection.is_public) {
      if (!req.user || (req.user.id !== collection.curator_id && !req.user.isAdmin)) {
        throw new NotFoundError('Collection not found');
      }
    }

    // Increment view count
    await query('UPDATE curated_collections SET view_count = view_count + 1 WHERE id = $1', [collection.id]);

    // Get assets in collection
    const assetsResult = await query(`
      SELECT
        a.asset_id as id,
        a.name,
        a.slug,
        a.asset_type as type,
        a.houdini_context as context,
        a.description,
        a.tags,
        a.download_count,
        a.favorite_count,
        a.latest_version as version,
        u.username as owner,
        u.avatar_url as owner_avatar,
        v.thumbnail_url,
        cca.curator_note,
        cca.position
      FROM curated_collection_assets cca
      JOIN assets a ON cca.asset_id = a.id
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE cca.collection_id = $1 AND a.is_public = true
      ORDER BY cca.position ASC
      LIMIT $2 OFFSET $3
    `, [collection.id, parseInt(limit), offset]);

    res.json({
      collection: {
        id: collection.collection_id,
        name: collection.name,
        slug: collection.slug,
        description: collection.description,
        coverImage: collection.cover_image,
        isFeatured: collection.is_featured,
        isStaffPick: collection.is_staff_pick,
        assetCount: collection.asset_count,
        viewCount: collection.view_count + 1,
        createdAt: collection.created_at,
        curator: {
          username: collection.curator,
          displayName: collection.curator_display_name,
          avatarUrl: collection.curator_avatar,
        },
      },
      assets: assetsResult.rows.map(a => ({
        id: a.id,
        name: a.name,
        slug: `${a.owner}/${a.slug}`,
        type: a.type,
        context: a.context,
        description: a.description,
        tags: a.tags || [],
        downloadCount: a.download_count,
        favoriteCount: a.favorite_count,
        version: a.version,
        owner: a.owner,
        ownerAvatar: a.owner_avatar,
        thumbnailUrl: a.thumbnail_url,
        curatorNote: a.curator_note,
      })),
      pagination: {
        page: parseInt(page),
        limit: parseInt(limit),
        total: collection.asset_count,
        totalPages: Math.ceil(collection.asset_count / parseInt(limit)),
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /collections (Admin/Curator only)
 * Create a new curated collection
 */
router.post('/', authenticate, async (req, res, next) => {
  try {
    if (!req.user.isAdmin) {
      throw new ForbiddenError('Admin access required');
    }

    const { name, slug, description, coverImage, isFeatured, isStaffPick, isPublic } = req.body;

    if (!name || !slug) {
      throw new ValidationError('Name and slug are required');
    }

    const result = await query(`
      INSERT INTO curated_collections (name, slug, description, cover_image, curator_id, is_featured, is_staff_pick, is_public)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
      RETURNING collection_id as id, name, slug, description, cover_image, is_featured, is_staff_pick, is_public
    `, [name, slug, description, coverImage, req.user.id, isFeatured || false, isStaffPick || false, isPublic !== false]);

    res.status(201).json({
      collection: {
        id: result.rows[0].id,
        name: result.rows[0].name,
        slug: result.rows[0].slug,
        description: result.rows[0].description,
        coverImage: result.rows[0].cover_image,
        isFeatured: result.rows[0].is_featured,
        isStaffPick: result.rows[0].is_staff_pick,
        isPublic: result.rows[0].is_public,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /collections/:slug (Admin/Curator only)
 * Update a collection
 */
router.put('/:slug', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;

    // Check ownership
    const checkResult = await query('SELECT id, curator_id FROM curated_collections WHERE slug = $1', [slug]);
    if (checkResult.rows.length === 0) {
      throw new NotFoundError('Collection not found');
    }

    const collection = checkResult.rows[0];
    if (collection.curator_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only edit your own collections');
    }

    const { name, description, coverImage, position, isFeatured, isStaffPick, isPublic } = req.body;

    const result = await query(`
      UPDATE curated_collections
      SET name = COALESCE($1, name),
          description = COALESCE($2, description),
          cover_image = COALESCE($3, cover_image),
          position = COALESCE($4, position),
          is_featured = COALESCE($5, is_featured),
          is_staff_pick = COALESCE($6, is_staff_pick),
          is_public = COALESCE($7, is_public),
          updated_at = NOW()
      WHERE slug = $8
      RETURNING collection_id as id, name, slug, description, cover_image, is_featured, is_staff_pick, is_public
    `, [name, description, coverImage, position, isFeatured, isStaffPick, isPublic, slug]);

    res.json({
      collection: {
        id: result.rows[0].id,
        name: result.rows[0].name,
        slug: result.rows[0].slug,
        description: result.rows[0].description,
        coverImage: result.rows[0].cover_image,
        isFeatured: result.rows[0].is_featured,
        isStaffPick: result.rows[0].is_staff_pick,
        isPublic: result.rows[0].is_public,
      },
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /collections/:slug/assets (Admin/Curator only)
 * Add an asset to a collection
 */
router.post('/:slug/assets', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { assetSlug, curatorNote, position } = req.body;

    // Check ownership
    const checkResult = await query('SELECT id, curator_id FROM curated_collections WHERE slug = $1', [slug]);
    if (checkResult.rows.length === 0) {
      throw new NotFoundError('Collection not found');
    }

    const collection = checkResult.rows[0];
    if (collection.curator_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only edit your own collections');
    }

    // Get asset
    const [owner, assetSlugPart] = assetSlug.split('/');
    const assetResult = await query(`
      SELECT a.id FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2
    `, [owner.toLowerCase(), assetSlugPart.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    // Add to collection
    await query(`
      INSERT INTO curated_collection_assets (collection_id, asset_id, curator_note, position, added_by)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT (collection_id, asset_id) DO UPDATE
      SET curator_note = $3, position = $4
    `, [collection.id, assetResult.rows[0].id, curatorNote, position || 0, req.user.id]);

    // Update count
    await query(`
      UPDATE curated_collections SET asset_count = (
        SELECT COUNT(*) FROM curated_collection_assets WHERE collection_id = $1
      ) WHERE id = $1
    `, [collection.id]);

    res.status(201).json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /collections/:slug/assets/:assetSlug (Admin/Curator only)
 * Remove an asset from a collection
 */
router.delete('/:slug/assets/:assetSlug(*)', authenticate, async (req, res, next) => {
  try {
    const { slug, assetSlug } = req.params;

    // Check ownership
    const checkResult = await query('SELECT id, curator_id FROM curated_collections WHERE slug = $1', [slug]);
    if (checkResult.rows.length === 0) {
      throw new NotFoundError('Collection not found');
    }

    const collection = checkResult.rows[0];
    if (collection.curator_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only edit your own collections');
    }

    // Get asset
    const decoded = decodeURIComponent(assetSlug);
    const [owner, assetSlugPart] = decoded.split('/');
    const assetResult = await query(`
      SELECT a.id FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2
    `, [owner.toLowerCase(), assetSlugPart.toLowerCase()]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    // Remove from collection
    await query(
      'DELETE FROM curated_collection_assets WHERE collection_id = $1 AND asset_id = $2',
      [collection.id, assetResult.rows[0].id]
    );

    // Update count
    await query(`
      UPDATE curated_collections SET asset_count = (
        SELECT COUNT(*) FROM curated_collection_assets WHERE collection_id = $1
      ) WHERE id = $1
    `, [collection.id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /collections/:slug (Admin/Curator only)
 * Delete a collection
 */
router.delete('/:slug', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;

    // Check ownership
    const checkResult = await query('SELECT id, curator_id FROM curated_collections WHERE slug = $1', [slug]);
    if (checkResult.rows.length === 0) {
      throw new NotFoundError('Collection not found');
    }

    const collection = checkResult.rows[0];
    if (collection.curator_id !== req.user.id && !req.user.isAdmin) {
      throw new ForbiddenError('You can only delete your own collections');
    }

    await query('DELETE FROM curated_collections WHERE id = $1', [collection.id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

export default router;
