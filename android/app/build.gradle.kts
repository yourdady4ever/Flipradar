import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.flipradar.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.flipradar.app"
        minSdk = 26
        targetSdk = 36
        // Keep in sync with scripts/build-offline.sh (VERSION_CODE / VERSION_NAME) and AppInfo.kt.
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            // No code shrinking by default: the app has no third-party dependencies to strip.
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        // Pure-JVM unit tests (Money, ApiModels, ServerUrl) never touch android.* APIs.
        unitTests.isReturnDefaultValues = true
    }

    lint {
        abortOnError = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    // Intentionally no runtime dependencies besides the Kotlin stdlib (added by the Kotlin plugin).
    testImplementation("junit:junit:4.13.2")
    // The android.jar used for local unit tests only contains stubs for org.json; use the real one.
    testImplementation("org.json:json:20250517")
}
