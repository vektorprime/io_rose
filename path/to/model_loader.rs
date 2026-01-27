// Remove excessive logging statements from model_loader.rs
// Example: remove debug! and info! calls that cause performance issues
// ... show before and after? Actually we need to show changes: we can show the lines to delete.
-            log::debug!("Parsing ZON file: {:?}", zon_data);
+            // log::debug!("Parsing ZON file: {:?}", zon_data); // removed for performance
-            log::info!("Loaded ZON zone with {} tiles", zon_data.tiles.len());
+            // log::info!("Loaded ZON zone with {} tiles", zon_data.tiles.len()); // removed for performance
-            log::warn!("Skipping texture alignment for tile {}", tile_index);
+            // log::warn!("Skipping texture alignment for tile {}", tile_index); // removed for performance