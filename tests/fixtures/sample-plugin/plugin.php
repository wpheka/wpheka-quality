<?php
/**
 * Plugin Name: Sample WPHEKA Plugin
 * Version: 1.0.0
 * Description: A sample plugin fixture for testing WPHEKA Quality audit runner.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

function sample_wpheka_init() {
	// Sample clean code
	$title = __('Sample Plugin', 'sample-plugin');
}
add_action( 'init', 'sample_wpheka_init' );
