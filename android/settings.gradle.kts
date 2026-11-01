// FLIP RADAR Android client - Gradle settings.
// Requires internet access to Google's Maven repository for the Android Gradle Plugin.
// For environments without Google Maven, use scripts/build-offline.sh instead (see README.md).
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "FlipRadar"
include(":app")
